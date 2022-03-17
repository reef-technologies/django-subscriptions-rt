from rest_framework.generics import CreateAPIView, DestroyAPIView, ListAPIView, ListCreateAPIView, RetrieveAPIView, \
                                    RetrieveUpdateDestroyAPIView, UpdateAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated

from ..models import Plan, Subscription
from .serializers import PlanSerializer, SubscriptionSerializer
from rest_framework.schemas.openapi import AutoSchema


class PlanListView(ListAPIView):
    permission_classes = AllowAny,
    queryset = Plan.objects.filter(is_enabled=True)
    serializer_class = PlanSerializer
    schema = AutoSchema()
    ordering = '-id',


class SubscriptionListView(ListCreateAPIView):
    permission_classes = IsAuthenticated,
    queryset = Subscription.objects.active().select_related('plan')
    serializer_class = SubscriptionSerializer
    schema = AutoSchema()
    ordering = '-end', '-id',


# class Subscription
