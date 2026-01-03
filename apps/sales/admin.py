"""
Admin configuration for sales app.
"""
from django.contrib import admin
from .models import ShopSettings, Shift, Sale, SaleItem


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = ['total']


@admin.register(ShopSettings)
class ShopSettingsAdmin(admin.ModelAdmin):
    list_display = ['shop', 'receipt_printer_type', 'enable_cash_payment', 'enable_ecash_payment']
    list_filter = ['receipt_printer_type']


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ['id', 'shop', 'attendant', 'status', 'start_time', 'end_time', 'opening_cash', 'closing_cash']
    list_filter = ['status', 'shop', 'start_time']
    readonly_fields = ['start_time']


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ['sale_number', 'shop', 'attendant', 'payment_method', 'status', 'total', 'created_at']
    list_filter = ['status', 'payment_method', 'shop', 'created_at']
    search_fields = ['sale_number', 'paystack_reference']
    readonly_fields = ['sale_number', 'created_at', 'completed_at']
    inlines = [SaleItemInline]
