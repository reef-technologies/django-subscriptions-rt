from django.contrib import admin

from .models import Plan, Quota, Resource, Subscription, SubscriptionPayment, SubscriptionPaymentRefund, Tax, Usage, Tier, Feature


class QuotaInline(admin.TabularInline):
    model = Quota
    extra = 0


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = 'codename', 'description',
    search_fields = 'codename',
    ordering = 'codename',


@admin.register(Tier)
class TierAdmin(admin.ModelAdmin):
    list_display = 'codename', 'description', 'is_default', 'level',
    search_fields = 'codename',
    ordering = '-level', 'codename',


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = 'codename', 'name', 'is_recurring', 'charge_amount', 'charge_period', 'max_duration', 'tier', 'is_enabled',
    list_filter = 'is_enabled',
    search_fields = 'codename', 'name',
    ordering = '-pk',
    inlines = QuotaInline,

    @admin.display(boolean=True, description='recurring')
    def is_recurring(self, instance):
        return instance.is_recurring()


class SubscriptionPaymentInline(admin.StackedInline):
    model = SubscriptionPayment
    extra = 0
    fields = 'uid', 'created', 'status', 'amount', 'quantity', 'provider_codename', 'provider_transaction_id', 'subscription_start', 'subscription_end', 'metadata',
    readonly_fields = 'created', 'metadata',
    ordering = '-created',


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = 'user', 'plan', 'auto_prolong', 'start', 'end',
    autocomplete_fields = 'user',
    list_filter = 'plan', 'auto_prolong', 'start', 'end',
    search_fields = 'user__email', 'user__first_name', 'user__last_name',
    inlines = [
        SubscriptionPaymentInline,
    ]
    ordering = '-start', 'uid',


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
    autocomplete_fields = 'user',
    list_filter = 'datetime', 'resource',
    search_fields = 'user__email', 'user__first_name', 'user__last_name',
    ordering = '-pk',


class SubscriptionPaymentRefundInline(admin.StackedInline):
    model = SubscriptionPaymentRefund
    extra = 0
    fields = 'uid', 'original_payment', 'created', 'status', 'amount', 'provider_codename', 'provider_transaction_id',
    readonly_fields = 'created',
    ordering = '-original_payment__subscription_end',


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = 'pk', 'status', 'created', 'amount', 'user', 'subscription_start', 'subscription_end',
    autocomplete_fields = 'user', 'subscription',
    list_filter = 'subscription__plan', 'status', 'created', 'updated', 'provider_codename',
    search_fields = 'uid', 'user__email', 'user__first_name', 'user__last_name', 'amount',
    inlines = [
        SubscriptionPaymentRefundInline,
    ]
    queryset = SubscriptionPayment.objects.select_related('subscription__plan')
    ordering = '-created',


@admin.register(SubscriptionPaymentRefund)
class SubscriptionPaymentRefundAdmin(admin.ModelAdmin):
    list_display = 'pk', 'status', 'original_payment', 'created', 'updated', 'amount'
    autocomplete_fields = 'original_payment',
    list_filter = 'status', 'created', 'updated',
    search_fields = 'original_payment__user__email',
    queryset = SubscriptionPaymentRefund.objects.select_related('original_payment__user')
    ordering = '-created',


@admin.register(Tax)
class TaxAdmin(admin.ModelAdmin):
    list_display = 'subscription_payment', 'amount',
    autocomplete_fields = 'subscription_payment',
    list_filter = 'subscription_payment__status',
    search_fields = 'subscription_payment__user__email',
    queryset = Tax.objects.select_related('subscription_payment')
    ordering = '-pk',
