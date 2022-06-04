from django.contrib import admin

from .models import Plan, Quota, Resource, Subscription, SubscriptionPayment, SubscriptionPaymentRefund, Tax, Usage


class QuotaInline(admin.TabularInline):
    model = Quota
    extra = 0


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = 'codename', 'name', 'charge_amount', 'charge_period', 'max_duration', 'is_enabled',
    list_filter = 'is_enabled',
    search_fields = 'codename', 'name',
    ordering = '-pk',
    inlines = QuotaInline,


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = 'user', 'plan', 'start', 'end',
    list_filter = 'plan', 'start', 'end',
    search_fields = 'user',
    ordering = '-pk',


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = 'codename', 'units',
    search_fields = 'codename',
    ordering = 'codename',


@admin.register(Quota)
class QuotaAdmin(admin.ModelAdmin):
    list_display = 'plan', 'resource', 'limit', 'recharge_period', 'burns_in',
    list_filter = 'plan', 'resource', 'recharge_period', 'burns_in',
    ordering = 'plan', 'resource', 'pk',


@admin.register(Usage)
class UsageAdmin(admin.ModelAdmin):
    list_display = 'user', 'resource', 'amount', 'datetime',
    list_filter = 'datetime', 'resource',
    search_fields = 'user',
    ordering = '-pk',


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = 'pk', 'status', 'created', 'updated', 'amount', 'user', 'subscription', 'subscription_start', 'subscription_end', 'provider_codename',
    list_filter = 'subscription__plan', 'status', 'created', 'updated', 'provider_codename',
    search_fields = 'user', 'amount',
    queryset = SubscriptionPayment.objects.select_related('subscription__plan')
    ordering = '-pk',


@admin.register(SubscriptionPaymentRefund)
class SubscriptionPaymentRefundAdmin(admin.ModelAdmin):
    list_display = 'pk', 'status', 'original_payment', 'created', 'updated', 'amount'
    list_filter = 'status', 'created', 'updated',
    search_fieds = 'original_payment__user'
    queryset = SubscriptionPaymentRefund.objects.select_related('original_payment__user')
    ordering = '-pk',


@admin.register(Tax)
class TaxAdmin(admin.ModelAdmin):
    list_display = 'subscription_payment', 'amount',
    list_filter = 'subscription_payment__status',
    search_fields = 'subscription_payment',
    queryset = Tax.objects.select_related('subscription_payment')
    ordering = '-pk',
