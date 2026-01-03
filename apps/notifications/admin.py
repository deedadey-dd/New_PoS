"""
Admin configuration for notifications app.
"""
from django.contrib import admin
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'notification_type', 'is_read', 'created_at']
    list_filter = ['notification_type', 'is_read', 'created_at']
    search_fields = ['title', 'message', 'user__email']
    readonly_fields = ['created_at', 'read_at']
    
    fieldsets = (
        ('Notification', {
            'fields': ('user', 'title', 'message', 'notification_type')
        }),
        ('Reference', {
            'fields': ('reference_type', 'reference_id')
        }),
        ('Status', {
            'fields': ('is_read', 'read_at', 'created_at')
        }),
    )
