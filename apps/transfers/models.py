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
        # Safely check if both locations are set before comparing
        try:
            source = self.source_location
            dest = self.destination_location
            if source and dest and source == dest:
                raise ValidationError("Source and destination locations must be different.")
        except Transfer.source_location.RelatedObjectDoesNotExist:
            pass  # Will be caught by form validation
        except Transfer.destination_location.RelatedObjectDoesNotExist:
            pass  # Will be caught by form validation
    
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
    
    # Access control methods
    def user_is_source(self, user):
        """Check if user belongs to source location."""
        if not user or not user.is_authenticated:
            return False
        # Admin can act as source
        if user.role and user.role.name == 'ADMIN':
            return True
        # Check if user's location matches source
        if user.location and user.location == self.source_location:
            return True
        # Check if user's role matches source location type
        role_location_map = {
            'PRODUCTION_MANAGER': 'PRODUCTION',
            'STORES_MANAGER': 'STORES',
            'SHOP_MANAGER': 'SHOP',
        }
        if user.role:
            user_location_type = role_location_map.get(user.role.name)
            if user_location_type == self.source_location.location_type:
                return True
        return False
    
    def user_is_destination(self, user):
        """Check if user belongs to destination location."""
        if not user or not user.is_authenticated:
            return False
        # Admin can act as destination
        if user.role and user.role.name == 'ADMIN':
            return True
        # Check if user's location matches destination
        if user.location and user.location == self.destination_location:
            return True
        # Check if user's role matches destination location type
        role_location_map = {
            'PRODUCTION_MANAGER': 'PRODUCTION',
            'STORES_MANAGER': 'STORES',
            'SHOP_MANAGER': 'SHOP',
        }
        if user.role:
            user_location_type = role_location_map.get(user.role.name)
            if user_location_type == self.destination_location.location_type:
                return True
        return False
    
    def user_can_view(self, user):
        """Check if user can view this transfer."""
        if not user or not user.is_authenticated:
            return False
        # Admin can view all
        if user.role and user.role.name == 'ADMIN':
            return True
        return self.user_is_source(user) or self.user_is_destination(user)
    
    def user_can_send(self, user):
        """Check if user can send this transfer."""
        return self.can_send and self.user_is_source(user)
    
    def user_can_receive(self, user):
        """Check if user can receive this transfer."""
        return self.can_receive and self.user_is_destination(user)
    
    def user_can_cancel(self, user):
        """Check if user can cancel this transfer."""
        if self.status == 'DRAFT':
            return self.user_is_source(user)
        elif self.status == 'SENT':
            # Destination can cancel/reject a sent transfer
            return self.user_is_destination(user)
        return False
    
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
        """Mark the transfer as disputed and restore discrepancy to sender."""
        if not self.can_dispute:
            raise ValidationError(f"Cannot dispute transfer in {self.status} status.")
        
        # Restore discrepancy to sender's inventory
        for item in self.items.all():
            discrepancy = item.quantity_sent - item.quantity_received
            
            if discrepancy > 0:
                # Create reversal entry at source location (add back the missing quantity)
                InventoryLedger.objects.create(
                    tenant=self.tenant,
                    product=item.product,
                    batch=item.batch,
                    location=self.source_location,
                    transaction_type='DISPUTE_REVERSAL',
                    quantity=discrepancy,  # Positive = adding back
                    unit_cost=item.unit_cost,
                    reference_type='Transfer',
                    reference_id=self.pk,
                    notes=f"Dispute reversal: {discrepancy} units restored. Reason: {reason[:100]}",
                    created_by=user
                )
        
        self.status = 'DISPUTED'
        self.dispute_reason = reason
        self.save()
        
        # Notify both locations
        self._create_notification(
            f"Transfer {self.transfer_number} disputed",
            f"Dispute reason: {reason[:100]}... Discrepancy restored to sender.",
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
        if self.unit_cost is None:
            return Decimal('0')
        return self.quantity_sent * self.unit_cost


class StockRequest(TenantModel):
    """
    Stock request from downstream to upstream location.
    Allows Shop to request from Stores, and Stores to request from Production.
    Workflow: PENDING → APPROVED/REJECTED → CONVERTED (to transfer)
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CONVERTED', 'Converted to Transfer'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    # Request rules: who can request from whom
    REQUEST_RULES = {
        'SHOP': ['STORES'],       # Shop can request from Stores
        'STORES': ['PRODUCTION'], # Stores can request from Production
    }
    
    request_number = models.CharField(max_length=50)
    
    requesting_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='requests_sent',
        help_text="Location requesting the stock"
    )
    supplying_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='requests_received',
        help_text="Location being requested from"
    )
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    # Audit fields
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='stock_requests_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_requests_approved'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    
    notes = models.TextField(blank=True, help_text="Request notes/reason")
    rejection_reason = models.TextField(blank=True)
    
    # Link to resulting transfer when converted
    resulting_transfer = models.ForeignKey(
        Transfer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_request'
    )
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['tenant', 'request_number']
    
    def __str__(self):
        return f"Request {self.request_number}: {self.requesting_location.name} ← {self.supplying_location.name}"
    
    def save(self, *args, **kwargs):
        # Auto-generate request number
        if not self.request_number:
            last_request = StockRequest.objects.filter(tenant=self.tenant).order_by('-id').first()
            if last_request and last_request.request_number:
                try:
                    last_num = int(last_request.request_number.replace('REQ', ''))
                    self.request_number = f"REQ{last_num + 1:06d}"
                except ValueError:
                    self.request_number = "REQ000001"
            else:
                self.request_number = "REQ000001"
        super().save(*args, **kwargs)
    
    def clean(self):
        # Validate request direction
        try:
            req_loc = self.requesting_location
            sup_loc = self.supplying_location
            if req_loc and sup_loc:
                if req_loc == sup_loc:
                    raise ValidationError("Requesting and supplying locations must be different.")
                
                allowed_suppliers = self.REQUEST_RULES.get(req_loc.location_type, [])
                if sup_loc.location_type not in allowed_suppliers:
                    raise ValidationError(
                        f"{req_loc.location_type} can only request from: {', '.join(allowed_suppliers)}. "
                        f"Cannot request from {sup_loc.location_type}."
                    )
        except (StockRequest.requesting_location.RelatedObjectDoesNotExist,
                StockRequest.supplying_location.RelatedObjectDoesNotExist):
            pass
    
    @property
    def total_items(self):
        return self.items.count()
    
    @property
    def can_approve(self):
        return self.status == 'PENDING'
    
    @property
    def can_reject(self):
        return self.status == 'PENDING'
    
    @property
    def can_convert(self):
        return self.status == 'APPROVED'
    
    @property
    def can_cancel(self):
        return self.status == 'PENDING'
    
    # Access control methods
    def user_is_requestor(self, user):
        """Check if user belongs to requesting location."""
        if not user or not user.is_authenticated:
            return False
        if user.role and user.role.name == 'ADMIN':
            return True
        if user.location and user.location == self.requesting_location:
            return True
        role_location_map = {
            'PRODUCTION_MANAGER': 'PRODUCTION',
            'STORES_MANAGER': 'STORES',
            'SHOP_MANAGER': 'SHOP',
        }
        if user.role:
            user_location_type = role_location_map.get(user.role.name)
            if user_location_type == self.requesting_location.location_type:
                return True
        return False
    
    def user_is_supplier(self, user):
        """Check if user belongs to supplying location."""
        if not user or not user.is_authenticated:
            return False
        if user.role and user.role.name == 'ADMIN':
            return True
        if user.location and user.location == self.supplying_location:
            return True
        role_location_map = {
            'PRODUCTION_MANAGER': 'PRODUCTION',
            'STORES_MANAGER': 'STORES',
            'SHOP_MANAGER': 'SHOP',
        }
        if user.role:
            user_location_type = role_location_map.get(user.role.name)
            if user_location_type == self.supplying_location.location_type:
                return True
        return False
    
    def user_can_view(self, user):
        """Check if user can view this request."""
        if not user or not user.is_authenticated:
            return False
        if user.role and user.role.name == 'ADMIN':
            return True
        return self.user_is_requestor(user) or self.user_is_supplier(user)
    
    def user_can_approve(self, user):
        """Check if user can approve this request."""
        return self.can_approve and self.user_is_supplier(user)
    
    def user_can_reject(self, user):
        """Check if user can reject this request."""
        return self.can_reject and self.user_is_supplier(user)
    
    def user_can_convert(self, user):
        """Check if user can convert this request to a transfer."""
        return self.can_convert and self.user_is_supplier(user)
    
    def user_can_cancel(self, user):
        """Check if user can cancel this request."""
        return self.can_cancel and self.user_is_requestor(user)
    
    def approve(self, user):
        """Approve the stock request."""
        if not self.can_approve:
            raise ValidationError(f"Cannot approve request in {self.status} status.")
        
        self.status = 'APPROVED'
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save()
        
        # Notify requestor
        self._create_notification(
            f"Stock Request {self.request_number} approved",
            f"Your stock request has been approved by {self.supplying_location.name}.",
            'REQUEST_APPROVED',
            self.requesting_location
        )
    
    def reject(self, user, reason):
        """Reject the stock request."""
        if not self.can_reject:
            raise ValidationError(f"Cannot reject request in {self.status} status.")
        
        self.status = 'REJECTED'
        self.rejection_reason = reason
        self.save()
        
        # Notify requestor
        self._create_notification(
            f"Stock Request {self.request_number} rejected",
            f"Reason: {reason[:100]}...",
            'REQUEST_REJECTED',
            self.requesting_location
        )
    
    def convert_to_transfer(self, user):
        """Convert approved request to a draft transfer."""
        if not self.can_convert:
            raise ValidationError(f"Cannot convert request in {self.status} status.")
        
        # Create transfer
        transfer = Transfer.objects.create(
            tenant=self.tenant,
            source_location=self.supplying_location,
            destination_location=self.requesting_location,
            created_by=user,
            notes=f"Created from Stock Request {self.request_number}"
        )
        
        # Copy items to transfer
        for item in self.items.all():
            TransferItem.objects.create(
                tenant=self.tenant,
                transfer=transfer,
                product=item.product,
                quantity_requested=item.quantity_requested if item.quantity_requested > 0 else Decimal('1'),
                notes=item.notes
            )
        
        # Update request status
        self.status = 'CONVERTED'
        self.resulting_transfer = transfer
        self.save()
        
        return transfer
    
    def cancel(self, user):
        """Cancel the stock request."""
        if not self.can_cancel:
            raise ValidationError(f"Cannot cancel request in {self.status} status.")
        
        self.status = 'CANCELLED'
        self.save()
    
    def _create_notification(self, title, message, notification_type, location):
        """Create notifications for users at a location."""
        from apps.notifications.models import Notification
        
        users = User.objects.filter(
            tenant=self.tenant,
            is_active=True
        ).filter(
            models.Q(location=location) | models.Q(role__name='ADMIN')
        ).distinct()
        
        for user in users:
            Notification.objects.create(
                tenant=self.tenant,
                user=user,
                title=title,
                message=message,
                notification_type=notification_type,
                reference_type='StockRequest',
                reference_id=self.pk
            )


class StockRequestItem(TenantModel):
    """
    Individual item in a stock request.
    Quantity can be 0 to indicate "as much as possible".
    """
    request = models.ForeignKey(
        StockRequest,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='stock_request_items'
    )
    
    quantity_requested = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text="Quantity requested (0 = as much as possible)"
    )
    
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['id']
    
    def __str__(self):
        qty_str = str(self.quantity_requested) if self.quantity_requested > 0 else "any"
        return f"{self.product.name} x {qty_str}"

