from django.db import models
from django.conf import settings
from apps.core.models import TenantModel

class UserActivity(TenantModel):
    ACTION_CHOICES = [
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Delete'),
        ('OTHER', 'Other'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    path = models.CharField(max_length=255, help_text="URL path of the action")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_info = models.CharField(max_length=255, null=True, blank=True, help_text="User Agent / Device Info")
    details = models.TextField(blank=True, help_text="Additional details about the action")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name_plural = 'User Activities'

    def __str__(self):
        username = self.user.get_full_name() if self.user else 'Unknown User'
        return f"{username} - {self.get_action_display()} at {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}"
