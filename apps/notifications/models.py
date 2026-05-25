"""
Notification models for the POS system.
In-app notifications for users about transfers, stock alerts, etc.
"""
from django.db import models
from django.utils import timezone

from apps.core.models import TenantModel, User, Location

class Notification(TenantModel):
    """
    In-app notification for users.
    """
    TYPE_CHOICES = [
        ('TRANSFER_SENT', 'Transfer Sent'),
        ('TRANSFER_RECEIVED', 'Transfer Received'),
        ('TRANSFER_DISPUTED', 'Transfer Disputed'),
        ('STOCK_ADJUSTMENT', 'Stock Adjustment'),
        ('LOW_STOCK', 'Low Stock Alert'),
        ('EXPIRY_WARNING', 'Expiry Warning'),
        ('SUBSCRIPTION_EXPIRY', 'Subscription Expiry'),
        ('SUBSCRIPTION_DEACTIVATED', 'Subscription Deactivated'),
        ('ACCOUNT_LOCKED', 'Account Locked'),
        ('PRICE_CHANGE', 'Price Change Alert'),
        ('SYSTEM', 'System Notification'),
    ]
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default='SYSTEM')
    
    # Optional reference to related object
    reference_type = models.CharField(max_length=50, blank=True)
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.title} - {self.user.email}"
    
    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save()
    
    @classmethod
    def get_unread_count(cls, user):
        """Get count of unread notifications for a user."""
        return cls.objects.filter(user=user, is_read=False).count()
    
    @classmethod
    def get_recent_for_user(cls, user, limit=10):
        """Get recent notifications for a user."""
        return cls.objects.filter(user=user).select_related('tenant')[:limit]


class BulletinPost(TenantModel):
    """
    Organization-wide or targeted bulletin board post.
    """
    POST_TYPES = [
        ('ANNOUNCEMENT', 'Announcement'),
        ('PRICE_ALERT', 'Price Alert'),
        ('GENERAL', 'General'),
    ]


    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='bulletin_posts'
    )
    
    title = models.CharField(max_length=255)
    body = models.TextField()
    post_type = models.CharField(max_length=20, choices=POST_TYPES, default='GENERAL')
    
    # Targeting
    target_roles = models.ManyToManyField(
        'core.Role',
        blank=True,
        related_name='targeted_bulletins',
        help_text="If none selected, defaults to all roles."
    )
    target_locations = models.ManyToManyField(
        'core.Location',
        blank=True,
        related_name='targeted_bulletins',
        help_text="If none selected, defaults to all locations."
    )
    
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.get_post_type_display()})"


class BulletinRead(models.Model):
    """
    Tracks which users have read/dismissed which bulletin posts.
    """
    bulletin_post = models.ForeignKey(
        BulletinPost,
        on_delete=models.CASCADE,
        related_name='reads'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='bulletin_reads'
    )
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('bulletin_post', 'user')

    def __str__(self):
        return f"{self.user.email} read {self.bulletin_post.title}"


