"""
Admin configuration for transfers app.
"""
from django.contrib import admin
from .models import Transfer, TransferItem


class TransferItemInline(admin.TabularInline):
    model = TransferItem
    extra = 1
    readonly_fields = ['quantity_sent', 'quantity_received']
    fields = ['product', 'batch', 'quantity_requested', 'quantity_sent', 'quantity_received', 'unit_cost', 'notes']


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ['transfer_number', 'source_location', 'destination_location', 'status', 'created_by', 'created_at']
    list_filter = ['status', 'source_location', 'destination_location', 'created_at']
    search_fields = ['transfer_number', 'notes']
    readonly_fields = ['transfer_number', 'created_at', 'sent_at', 'received_at']
    inlines = [TransferItemInline]
    
    fieldsets = (
        ('Transfer Information', {
            'fields': ('transfer_number', 'source_location', 'destination_location', 'status')
        }),
        ('Dates', {
            'fields': ('created_at', 'sent_at', 'received_at')
        }),
        ('Users', {
            'fields': ('created_by', 'sent_by', 'received_by')
        }),
        ('Notes', {
            'fields': ('notes', 'dispute_reason', 'resolution_notes')
        }),
    )
