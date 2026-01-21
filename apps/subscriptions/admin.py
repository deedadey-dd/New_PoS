"""
Admin configuration for the subscriptions app.
"""
from django.contrib import admin
from .models import (
    SubscriptionPlan,
    TenantPricingOverride,
    SubscriptionPayment,
    TenantManagerAssignment,
    SubscriptionNotificationLog,
)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'base_price', 'max_shops', 'is_active', 'display_order']
    list_filter = ['is_active']
    search_fields = ['name', 'code']
    ordering = ['display_order', 'name']


@admin.register(TenantPricingOverride)
class TenantPricingOverrideAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'onboarding_fee', 'monthly_price', 'discount_percentage', 'created_at']
    list_filter = ['created_at']
    search_fields = ['tenant__name']
    raw_id_fields = ['tenant']


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ['receipt_number', 'tenant', 'payment_type', 'amount', 'status', 'created_at']
    list_filter = ['status', 'payment_type', 'payment_method', 'created_at']
    search_fields = ['receipt_number', 'tenant__name', 'paystack_reference']
    raw_id_fields = ['tenant']
    date_hierarchy = 'created_at'
    readonly_fields = ['receipt_number', 'created_at']


@admin.register(TenantManagerAssignment)
class TenantManagerAssignmentAdmin(admin.ModelAdmin):
    list_display = ['manager', 'tenant', 'is_primary', 'assigned_at']
    list_filter = ['is_primary', 'assigned_at']
    search_fields = ['manager__email', 'tenant__name']
    raw_id_fields = ['manager', 'tenant']


@admin.register(SubscriptionNotificationLog)
class SubscriptionNotificationLogAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'notification_type', 'channel', 'is_sent', 'sent_at', 'created_at']
    list_filter = ['notification_type', 'channel', 'is_sent', 'created_at']
    search_fields = ['tenant__name', 'recipient_email', 'recipient_phone']
    raw_id_fields = ['tenant']
    date_hierarchy = 'created_at'

