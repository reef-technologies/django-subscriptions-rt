from logging import getLogger
from typing import Type

from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.schemas.openapi import AutoSchema
from subscriptions.functions import get_remaining_amount

from ..defaults import DEFAULT_SUBSCRIPTIONS_SUCCESS_URL
from ..exceptions import PaymentError, SubscriptionError
from ..models import Plan, Subscription
from ..providers import Provider, get_provider, get_providers
from ..validators import get_validators
from .serializers import PaymentProviderListSerializer, PlanSerializer, ResourcesSerializer, SubscriptionSelectSerializer, SubscriptionSerializer, WebhookSerializer

log = getLogger(__name__)


class PlanListView(ListAPIView):
    permission_classes = AllowAny,
    queryset = Plan.objects.filter(is_enabled=True)
    serializer_class = PlanSerializer
    schema = AutoSchema()
    ordering = '-id',


class PaymentProviderListView(GenericAPIView):
    permission_classes = AllowAny,
    exposed_info_keys = 'description',
    serializer_class = PaymentProviderListSerializer

    def get(self, request, *args, **kwargs) -> Response:
        serializer = self.serializer_class({
            'providers': [
                {'name': provider.codename}
                for provider in get_providers()
                if provider.is_enabled
            ],
        })

        return Response(serializer.data)


class SubscriptionListView(ListAPIView):
    permission_classes = IsAuthenticated,
    serializer_class = SubscriptionSerializer
    schema = AutoSchema()
    ordering = '-end', '-id',

    def get_queryset(self):
        return Subscription.objects.active().select_related('plan').filter(user=self.request.user)


class SubscriptionSelectView(GenericAPIView):
    permission_classes = IsAuthenticated,
    serializer_class = SubscriptionSelectSerializer
    schema = AutoSchema()

    @classmethod
    def select_payment_provider(cls) -> Type[Provider]:
        return get_provider()

    def post(self, request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # TODO: handle quantity

        plan = serializer.validated_data['plan']
        active_subscriptions = request.user.subscriptions.active().order_by('end')

        for validator in get_validators():
            try:
                validator(active_subscriptions, plan)
            except SubscriptionError as exc:
                raise PermissionDenied(detail=str(exc)) from exc

        provider = self.select_payment_provider()
        quantity = serializer.validated_data['quantity']
        charge_params = dict(
            user=request.user,
            plan=plan,
            quantity=quantity,
        )
        try:
            provider.charge_offline(**charge_params)
            redirect_url = getattr(settings, 'SUBSCRIPTIONS_SUCCESS_URL', DEFAULT_SUBSCRIPTIONS_SUCCESS_URL)
        except Exception as exc:
            if not isinstance(exc, (PaymentError, NotImplementedError)):
                log.exception('Offline charge error')
            redirect_url = provider.charge_online(**charge_params)

        return Response(self.serializer_class({
            'redirect_url': redirect_url,
            'quantity': quantity,
            'plan': plan,
        }).data)


class PaymentWebhookView(GenericAPIView):
    permission_classes = AllowAny,
    schema = AutoSchema()
    serializer_class = WebhookSerializer

    def post(self, request, *args, **kwargs) -> Response:
        return self.provider.webhook(request=request, payload=request.data)


def build_payment_webhook_view(provider: Provider) -> GenericAPIView:
    codename = provider.codename

    class _PaymentWebhookView(PaymentWebhookView):
        schema = AutoSchema(operation_id_base=f'_{codename}_webhook')
        provider = get_provider(codename)

    return _PaymentWebhookView


class ResourcesView(GenericAPIView):
    permission_classes = IsAuthenticated,
    serializer_class = ResourcesSerializer
    pagination_class = None
    schema = AutoSchema()

    def get(self, request, *args, **kwargs) -> Response:
        return Response({
            resource.codename: amount for resource, amount
            in get_remaining_amount(request.user).items()
        })
