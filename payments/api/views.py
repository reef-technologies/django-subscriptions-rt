from django.conf import settings
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.schemas.openapi import AutoSchema
from rest_framework.views import APIView

from ..exceptions import ProviderNotFound
from ..models import Plan, Subscription
from ..providers import get_provider
from .serializers import PlanSerializer, SubscriptionSerializer


class PlanListView(ListAPIView):
    permission_classes = AllowAny,
    queryset = Plan.objects.filter(is_enabled=True)
    serializer_class = PlanSerializer
    schema = AutoSchema()
    ordering = '-id',


class PaymentProviderListView(APIView):
    permission_classes = AllowAny,
    exposed_info_keys = 'description',

    def get(self, request, format=None):
        providers = {
            provider: {key: info.get(key) for key in self.exposed_info_keys}
            for provider, info in settings.PAYMENT_PROVIDERS.items()
        }
        return Response(providers)


class SubscriptionListView(ListCreateAPIView):
    permission_classes = IsAuthenticated,
    queryset = Subscription.objects.active().select_related('plan')
    serializer_class = SubscriptionSerializer
    schema = AutoSchema()
    ordering = '-end', '-id',


class PaymentWebhookView(APIView):
    permission_classes = AllowAny,

    def dispatch(self, request, *args, **kwargs):
        try:
            self.provider = get_provider(kwargs['payment_provider_name'])
        except ProviderNotFound:
            raise NotFound(detail='Provider not found')

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.provider.handle_webhook(request=request)
