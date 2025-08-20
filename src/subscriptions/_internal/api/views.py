import logging

from django.db import transaction
from django.http import QueryDict
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import DestroyAPIView, GenericAPIView, ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.schemas.openapi import AutoSchema
from rest_framework.views import APIView

from ..exceptions import InvalidOperation, SubscriptionError
from ..functions import get_remaining_amount
from ..models import Plan, Subscription, SubscriptionPayment, subscribe
from ..providers import Provider, get_provider, get_provider_by_codename, get_providers_fqns
from .exceptions import BadRequest
from .serializers import (
    PaymentProviderListSerializer,
    PlanSerializer,
    ResourcesSerializer,
    SubscriptionPaymentSerializer,
    SubscriptionSelectSerializer,
    SubscriptionSerializer,
    WebhookSerializer,
)

log = logging.getLogger(__name__)


class ResourceHeadersMixin(APIView):
    def finalize_response(self, request, *args, **kwargs) -> Response:
        response = super().finalize_response(request, *args, **kwargs)
        if request.user.is_authenticated:
            for resource, remains in get_remaining_amount(request.user).items():
                response[f"X-Resource-{resource.codename.capitalize()}"] = remains
        return response


class PlanListView(ListAPIView):
    permission_classes = (AllowAny,)
    queryset = Plan.objects.filter(is_enabled=True)
    serializer_class = PlanSerializer
    schema = AutoSchema()
    ordering = ("-id",)


class PaymentProviderListView(GenericAPIView):
    permission_classes = (AllowAny,)
    exposed_info_keys = ("description",)
    serializer_class = PaymentProviderListSerializer

    def get(self, request, *args, **kwargs) -> Response:
        serializer = self.serializer_class(
            {"providers": [{"name": get_provider(fqn).codename} for fqn in get_providers_fqns()]}
        )

        return Response(serializer.data)


class SubscriptionListView(ListAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = SubscriptionSerializer
    schema = AutoSchema()
    ordering = (
        "-end",
        "-uid",
    )

    def get_queryset(self):
        return Subscription.objects.active().select_related("plan").filter(user=self.request.user)


class SubscriptionView(DestroyAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = SubscriptionSerializer
    schema = AutoSchema()
    lookup_url_kwarg = "uid"

    def get_queryset(self):
        return Subscription.objects.active().filter(user=self.request.user)

    def perform_destroy(self, subscription):
        try:
            latest_payment = subscription.payments.latest()
            provider = get_provider_by_codename(latest_payment.provider_codename)
            provider.cancel(subscription)
        except InvalidOperation as exc:
            raise BadRequest from exc
        except SubscriptionPayment.DoesNotExist:
            subscription.auto_prolong = False
            subscription.save()


class SubscriptionSelectView(GenericAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = SubscriptionSelectSerializer
    schema = AutoSchema()

    @transaction.atomic(durable=True)
    def post(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        plan = serializer.validated_data["plan"]
        quantity = serializer.validated_data["quantity"]
        provider_codename = serializer.validated_data["provider"]
        provider = get_provider_by_codename(provider_codename)

        try:
            payment, redirect_url = subscribe(
                user=request.user,  # type: ignore[arg-type]
                plan=plan,
                quantity=quantity,
                provider=provider,
            )
        except SubscriptionError as exc:
            raise PermissionDenied(detail=str(exc)) from exc

        return Response(
            self.serializer_class(
                {
                    "redirect_url": redirect_url,
                    "status": payment.get_status_display().lower(),
                    "quantity": payment.quantity,
                    "plan": payment.plan,
                    "payment_id": payment.pk,
                    "provider": provider_codename,
                }
            ).data
        )


class PaymentWebhookView(GenericAPIView):
    permission_classes = (AllowAny,)
    schema = AutoSchema()
    serializer_class = WebhookSerializer
    provider: Provider

    def post(self, request, *args, **kwargs) -> Response:
        payload = request.data
        if isinstance(payload, QueryDict):
            payload = payload.dict()
        log.info("Webhook at %s received payload %s", request.build_absolute_uri(), payload)
        return self.provider.webhook(request=request, payload=payload)


def build_payment_webhook_view(provider: Provider) -> type[GenericAPIView]:
    _provider = provider

    class _PaymentWebhookView(PaymentWebhookView):
        schema = AutoSchema(operation_id_base=f"_{_provider.codename}_webhook")
        provider = _provider

    return _PaymentWebhookView


class ResourcesView(GenericAPIView):
    permission_classes = (IsAuthenticated,)
    serializer_class = ResourcesSerializer
    pagination_class = None
    schema = AutoSchema()

    def get(self, request: Request, *args, **kwargs) -> Response:
        entries = [
            {
                "codename": resource.codename,
                "amount": amount,
            }
            for resource, amount in get_remaining_amount(request.user).items()  # type: ignore[arg-type]
        ]
        serializer = self.get_serializer({"resources": entries})
        return Response(serializer.data)


class PaymentView(RetrieveAPIView):
    """
    GET request just asks backend to show whatever it has in database,
    while POST asks backend to force-fetch data from payment provider.
    """

    permission_classes = (IsAuthenticated,)
    serializer_class = SubscriptionPaymentSerializer
    schema = AutoSchema()
    queryset = SubscriptionPayment.objects.all()
    lookup_url_kwarg = "uid"

    def post(self, request, *args, **kwargs):
        """Fetch payment status from the provider and update status if needed"""
        payment = self.get_object()
        if payment.status == SubscriptionPayment.Status.PENDING:
            provider = get_provider_by_codename(payment.provider_codename)
            provider.check_payments([payment])

        return self.get(request, *args, **kwargs)
