"""
Admin configuration for inventory app.
"""
from django.contrib import admin
from .models import Category, Product, Batch, InventoryLedger, ShopPrice


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'tenant', 'is_active', 'created_at']
    list_filter = ['is_active', 'tenant']
    search_fields = ['name', 'description']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['sku', 'name', 'category', 'unit_of_measure', 'default_selling_price', 'is_active']
    list_filter = ['is_active', 'category', 'tenant']
    search_fields = ['sku', 'name', 'description']


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ['batch_number', 'product', 'location', 'current_quantity', 'unit_cost', 'expiry_date', 'status']
    list_filter = ['status', 'location', 'tenant']
    search_fields = ['batch_number', 'product__name']
    date_hierarchy = 'received_date'


@admin.register(InventoryLedger)
class InventoryLedgerAdmin(admin.ModelAdmin):
    list_display = ['product', 'location', 'transaction_type', 'quantity', 'unit_cost', 'created_at', 'created_by']
    list_filter = ['transaction_type', 'location', 'tenant']
    search_fields = ['product__name', 'notes']
    date_hierarchy = 'created_at'
    readonly_fields = ['product', 'batch', 'location', 'transaction_type', 'quantity', 'unit_cost', 
                       'reference_type', 'reference_id', 'notes', 'created_by', 'created_at']
    
    def has_change_permission(self, request, obj=None):
        return False  # Append-only
    
    def has_delete_permission(self, request, obj=None):
        return False  # Immutable


@admin.register(ShopPrice)
class ShopPriceAdmin(admin.ModelAdmin):
    list_display = ['product', 'location', 'selling_price', 'min_margin_percent', 'is_active', 'effective_from']
    list_filter = ['is_active', 'location', 'tenant']
    search_fields = ['product__name', 'product__sku']
