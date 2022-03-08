from django.contrib import admin

from .models import Plan, Quota, Resource, Subscription, Usage


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = 'codename', 'name', 'charge_amount', 'charge_period', 'subscription_duration', 'is_enabled',
    list_filter = 'is_enabled',
    search_fields = 'codename', 'name',
    ordering = '-pk',
    prepopulated_fields = {'slug': ('name',)}


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
