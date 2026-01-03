"""
Transfer models for the POS system.
Handles stock transfers between locations with state machine workflow.
"""
from django.db import models
from django.db.models import Sum
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal

from apps.core.models import TenantModel, Location, User
from apps.inventory.models import Product, Batch, InventoryLedger


class Transfer(TenantModel):
    """
    Transfer of stock between locations.
    Implements a state machine workflow: Draft → Sent → Received/Partial → Disputed → Closed
    """
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('SENT', 'Sent'),
        ('RECEIVED', 'Received'),
        ('PARTIAL', 'Partially Received'),
        ('DISPUTED', 'Disputed'),
        ('CLOSED', 'Closed'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    transfer_number = models.CharField(max_length=50)
    
    source_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='outgoing_transfers',
        help_text="Location sending the stock"
    )
    destination_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='incoming_transfers',
        help_text="Location receiving the stock"
    )
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    
    # Audit fields
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='transfers_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    sent_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transfers_sent'
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    
    received_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transfers_received'
    )
    received_at = models.DateTimeField(null=True, blank=True)
    
    notes = models.TextField(blank=True)
    dispute_reason = models.TextField(blank=True)
    resolution_notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['tenant', 'transfer_number']
    
    def __str__(self):
        return f"Transfer {self.transfer_number}: {self.source_location.name} → {self.destination_location.name}"
    
    def save(self, *args, **kwargs):
        # Auto-generate transfer number
        if not self.transfer_number:
            last_transfer = Transfer.objects.filter(tenant=self.tenant).order_by('-id').first()
            if last_transfer and last_transfer.transfer_number:
                try:
                    last_num = int(last_transfer.transfer_number.replace('TRF', ''))
                    self.transfer_number = f"TRF{last_num + 1:06d}"
                except ValueError:
                    self.transfer_number = "TRF000001"
            else:
                self.transfer_number = "TRF000001"
        super().save(*args, **kwargs)
    
    def clean(self):
        if self.source_location == self.destination_location:
            raise ValidationError("Source and destination locations must be different.")
    
    @property
    def total_items(self):
        return self.items.count()
    
    @property
    def total_quantity_requested(self):
        return self.items.aggregate(total=Sum('quantity_requested'))['total'] or Decimal('0')
    
    @property
    def total_quantity_received(self):
        return self.items.aggregate(total=Sum('quantity_received'))['total'] or Decimal('0')
    
    @property
    def can_send(self):
        return self.status == 'DRAFT' and self.items.exists()
    
    @property
    def can_receive(self):
        return self.status == 'SENT'
    
    @property
    def can_dispute(self):
        return self.status in ['SENT', 'PARTIAL']
    
    @property
    def can_close(self):
        return self.status in ['RECEIVED', 'DISPUTED']
    
    @property
    def can_cancel(self):
        return self.status == 'DRAFT'
    
    def send(self, user):
        """
        Send the transfer - deducts stock from source location.
        Creates TRANSFER_OUT inventory ledger entries.
        """
        if not self.can_send:
            raise ValidationError(f"Cannot send transfer in {self.status} status.")
        
        # Validate and deduct stock for each item
        for item in self.items.all():
            if item.batch:
                if item.batch.current_quantity < item.quantity_requested:
                    raise ValidationError(
                        f"Insufficient stock for {item.product.name} in batch {item.batch.batch_number}. "
                        f"Available: {item.batch.current_quantity}, Requested: {item.quantity_requested}"
                    )
            
            # Set sent quantity
            item.quantity_sent = item.quantity_requested
            item.save()
            
            # Create ledger entry for source (deduct)
            InventoryLedger.objects.create(
                tenant=self.tenant,
                product=item.product,
                batch=item.batch,
                location=self.source_location,
                transaction_type='TRANSFER_OUT',
                quantity=-item.quantity_sent,
                unit_cost=item.unit_cost,
                reference_type='Transfer',
                reference_id=self.pk,
                notes=f"Transfer to {self.destination_location.name}",
                created_by=user
            )
        
        self.status = 'SENT'
        self.sent_by = user
        self.sent_at = timezone.now()
        self.save()
        
        # Create notification for destination location users
        self._create_notification(
            f"Transfer {self.transfer_number} sent to your location",
            f"{self.source_location.name} has sent {self.total_items} item(s) to {self.destination_location.name}.",
            'TRANSFER_SENT',
            self.destination_location
        )
    
    def receive(self, user, items_received):
        """
        Receive the transfer - adds stock to destination location.
        Creates TRANSFER_IN inventory ledger entries.
        items_received: dict of {item_id: quantity_received}
        """
        if not self.can_receive:
            raise ValidationError(f"Cannot receive transfer in {self.status} status.")
        
        all_complete = True
        
        for item in self.items.all():
            received_qty = items_received.get(str(item.pk), item.quantity_sent)
            item.quantity_received = Decimal(str(received_qty))
            item.save()
            
            if item.quantity_received != item.quantity_sent:
                all_complete = False
            
            if item.quantity_received > 0:
                # Create or find batch at destination
                dest_batch = None
                if item.batch:
                    # Try to find matching batch at destination
                    dest_batch, created = Batch.objects.get_or_create(
                        tenant=self.tenant,
                        product=item.product,
                        location=self.destination_location,
                        batch_number=item.batch.batch_number,
                        defaults={
                            'unit_cost': item.unit_cost,
                            'initial_quantity': Decimal('0'),
                            'current_quantity': Decimal('0'),
                            'expiry_date': item.batch.expiry_date,
                            'manufacture_date': item.batch.manufacture_date,
                        }
                    )
                
                # Create ledger entry for destination (add)
                InventoryLedger.objects.create(
                    tenant=self.tenant,
                    product=item.product,
                    batch=dest_batch,
                    location=self.destination_location,
                    transaction_type='TRANSFER_IN',
                    quantity=item.quantity_received,
                    unit_cost=item.unit_cost,
                    reference_type='Transfer',
                    reference_id=self.pk,
                    notes=f"Transfer from {self.source_location.name}",
                    created_by=user
                )
        
        self.status = 'RECEIVED' if all_complete else 'PARTIAL'
        self.received_by = user
        self.received_at = timezone.now()
        self.save()
        
        # Create notification for source location
        status_text = "fully received" if all_complete else "partially received"
        self._create_notification(
            f"Transfer {self.transfer_number} {status_text}",
            f"{self.destination_location.name} has {status_text} the transfer.",
            'TRANSFER_RECEIVED',
            self.source_location
        )
    
    def dispute(self, user, reason):
        """Mark the transfer as disputed."""
        if not self.can_dispute:
            raise ValidationError(f"Cannot dispute transfer in {self.status} status.")
        
        self.status = 'DISPUTED'
        self.dispute_reason = reason
        self.save()
        
        # Notify both locations
        self._create_notification(
            f"Transfer {self.transfer_number} disputed",
            f"Dispute reason: {reason[:100]}...",
            'TRANSFER_DISPUTED',
            self.source_location
        )
    
    def close(self, user, resolution_notes=''):
        """Close the transfer."""
        if not self.can_close:
            raise ValidationError(f"Cannot close transfer in {self.status} status.")
        
        self.status = 'CLOSED'
        self.resolution_notes = resolution_notes
        self.save()
    
    def cancel(self, user):
        """Cancel a draft transfer."""
        if not self.can_cancel:
            raise ValidationError(f"Cannot cancel transfer in {self.status} status.")
        
        self.status = 'CANCELLED'
        self.save()
    
    def _create_notification(self, title, message, notification_type, location):
        """Create notifications for users at a location."""
        from apps.notifications.models import Notification
        
        # Get users at the location or admins
        users = User.objects.filter(
            tenant=self.tenant,
            is_active=True
        ).filter(
            models.Q(location=location) | models.Q(role__name__in=['ADMIN', 'STORES_MANAGER'])
        ).distinct()
        
        for user in users:
            Notification.objects.create(
                tenant=self.tenant,
                user=user,
                title=title,
                message=message,
                notification_type=notification_type,
                reference_type='Transfer',
                reference_id=self.pk
            )


class TransferItem(TenantModel):
    """
    Individual item in a transfer.
    Tracks requested, sent, and received quantities.
    """
    transfer = models.ForeignKey(
        Transfer,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='transfer_items'
    )
    batch = models.ForeignKey(
        Batch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transfer_items',
        help_text="Source batch (optional)"
    )
    
    quantity_requested = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    quantity_sent = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))]
    )
    quantity_received = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))]
    )
    
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
        default=Decimal('0'),
        blank=True,
        null=True,
        help_text="Unit cost at time of transfer (auto-filled from batch if blank)"
    )
    
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['id']
    
    def __str__(self):
        return f"{self.product.name} x {self.quantity_requested}"
    
    @property
    def discrepancy(self):
        """Difference between sent and received quantities."""
        return self.quantity_sent - self.quantity_received
    
    @property
    def total_cost(self):
        """Total cost based on sent quantity."""
        return self.quantity_sent * self.unit_cost
