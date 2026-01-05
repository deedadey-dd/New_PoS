"""
Accounting models for the POS system.
Handles cash transfers between shop managers and accountants.
"""
from django.db import models
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from decimal import Decimal

from apps.core.models import TenantModel, User, Location


class CashTransfer(TenantModel):
    """
    Cash transfer between users (typically Shop Manager → Accountant).
    Used for cash deposits and float disbursements.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending Confirmation'),
        ('CONFIRMED', 'Confirmed'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    TYPE_CHOICES = [
        ('DEPOSIT', 'Cash Deposit'),    # Shop → Accountant
        ('FLOAT', 'Float/Change'),       # Accountant → Shop
    ]
    
    # Transfer details
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    transfer_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='PENDING')
    
    # Sender
    from_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='cash_transfers_sent'
    )
    from_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='cash_transfers_from',
        null=True,
        blank=True
    )
    
    # Recipient
    to_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='cash_transfers_received'
    )
    to_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='cash_transfers_to',
        null=True,
        blank=True
    )
    
    # Notes
    notes = models.TextField(blank=True, help_text="Description or reference number")
    cancellation_reason = models.TextField(blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    
    # Confirmation user (person who confirms receipt)
    confirmed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cash_transfers_confirmed'
    )
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.get_transfer_type_display()} - {self.amount} ({self.get_status_display()})"
    
    @property
    def transfer_number(self):
        """Generate a transfer reference number."""
        return f"CT-{self.pk:06d}"
    
    def confirm(self, user):
        """Confirm the cash transfer receipt."""
        if self.status != 'PENDING':
            raise ValidationError("Only pending transfers can be confirmed.")
        
        # Verify the confirming user is the recipient
        if user != self.to_user and not user.is_superuser:
            if user.role and user.role.name != 'ADMIN':
                raise ValidationError("Only the recipient can confirm this transfer.")
        
        self.status = 'CONFIRMED'
        self.confirmed_at = timezone.now()
        self.confirmed_by = user
        self.save()
        
        # Create notification for sender
        from apps.notifications.models import Notification
        Notification.objects.create(
            tenant=self.tenant,
            user=self.from_user,
            title="Cash Transfer Confirmed",
            message=f"Your cash transfer of {self.amount} has been confirmed by {user.get_full_name() or user.email}.",
            notification_type='SYSTEM',
            reference_type='CashTransfer',
            reference_id=self.pk
        )
    
    def cancel(self, user, reason=''):
        """Cancel the cash transfer."""
        if self.status != 'PENDING':
            raise ValidationError("Only pending transfers can be cancelled.")
        
        self.status = 'CANCELLED'
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.save()
        
        # Notify both parties
        from apps.notifications.models import Notification
        for target_user in [self.from_user, self.to_user]:
            if target_user != user:
                Notification.objects.create(
                    tenant=self.tenant,
                    user=target_user,
                    title="Cash Transfer Cancelled",
                    message=f"Cash transfer of {self.amount} has been cancelled. Reason: {reason or 'Not specified'}",
                    notification_type='SYSTEM',
                    reference_type='CashTransfer',
                    reference_id=self.pk
                )
