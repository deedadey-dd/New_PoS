from django.contrib import admin
from .models import PaymentProviderConfig, ShopPaymentAssignment, ECashLedger, ECashWithdrawal


@admin.register(PaymentProviderConfig)
class PaymentProviderConfigAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'nickname', 'provider', 'is_active', 'created_at']
    list_filter = ['provider', 'is_active']
    search_fields = ['tenant__name', 'nickname']


@admin.register(ShopPaymentAssignment)
class ShopPaymentAssignmentAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'shop', 'provider_config', 'is_default', 'priority']
    list_filter = ['is_default']
    search_fields = ['shop__name', 'provider_config__nickname']


@admin.register(ECashLedger)
class ECashLedgerAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'transaction_type', 'amount', 'created_at', 'created_by']
    list_filter = ['transaction_type']
    search_fields = ['paystack_reference']
    date_hierarchy = 'created_at'


@admin.register(ECashWithdrawal)
class ECashWithdrawalAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'amount', 'status', 'withdrawn_by', 'created_at']
    list_filter = ['status']
    date_hierarchy = 'created_at'
