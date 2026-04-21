"""
Inventory models for the POS system.
Includes: Category, Product, Batch, InventoryLedger, ShopPrice
"""
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal

from apps.core.models import TenantModel, Location


class Category(TenantModel):
    """
    Product categories with hierarchical support.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subcategories'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']
        unique_together = ['tenant', 'name', 'parent']
    
    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name
    
    @property
    def full_path(self):
        """Get full category path."""
        if self.parent:
            return f"{self.parent.full_path} > {self.name}"
        return self.name


class Product(TenantModel):
    """
    Product master data.
    Cost is tracked at batch level, not product level.
    """
    UNIT_CHOICES = [
        ('UNIT', 'Unit'),
        ('KG', 'Kilogram'),
        ('G', 'Gram'),
        ('L', 'Litre'),
        ('ML', 'Millilitre'),
        ('M', 'Metre'),
        ('CM', 'Centimetre'),
        ('BOX', 'Box'),
        ('PACK', 'Pack'),
        ('DOZEN', 'Dozen'),
    ]
    
    sku = models.CharField(max_length=100, verbose_name="SKU/Barcode")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )
    unit_of_measure = models.CharField(max_length=10, choices=UNIT_CHOICES, default='UNIT')
    
    # Default pricing (can be overridden per shop via ShopPrice)
    default_selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    
    # Inventory settings
    reorder_level = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Alert when stock falls below this level"
    )
    
    # Product image
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Sync fields
    version = models.IntegerField(default=1)
    updated_by_device = models.CharField(max_length=100, null=True, blank=True)
    
    class Meta:
        ordering = ['name']
        unique_together = ['tenant', 'sku']
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['tenant', 'sku']),
            models.Index(fields=['tenant', 'category']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.sku})"
    
    def get_stock_at_location(self, location):
        """Get total stock quantity at a specific location."""
        from django.db.models import Sum
        result = InventoryLedger.objects.filter(
            product=self,
            location=location
        ).aggregate(total=Sum('quantity'))
        return result['total'] or Decimal('0.00')
    
    def get_total_stock(self):
        """Get total stock across all locations."""
        from django.db.models import Sum
        result = InventoryLedger.objects.filter(
            product=self
        ).aggregate(total=Sum('quantity'))
        return result['total'] or Decimal('0.00')


class Batch(TenantModel):
    """
    Batch/Lot tracking for products.
    Tracks unit cost, expiry, and can be split.
    """
    STATUS_CHOICES = [
        ('AVAILABLE', 'Available'),
        ('RESERVED', 'Reserved'),
        ('DEPLETED', 'Depleted'),
        ('EXPIRED', 'Expired'),
    ]
    
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='batches'
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='batches'
    )
    
    batch_number = models.CharField(max_length=100)
    
    # Costing
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Cost per unit for this batch"
    )
    
    # Quantity tracking
    initial_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    current_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))]
    )
    
    # Dates
    manufacture_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    received_date = models.DateField(default=timezone.now)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='AVAILABLE')
    notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = 'Batches'
        ordering = ['expiry_date', 'created_at']  # FEFO ordering
        unique_together = ['tenant', 'product', 'batch_number', 'location']
    
    def __str__(self):
        return f"{self.product.name} - Batch {self.batch_number}"
    
    @property
    def is_expired(self):
        """Check if batch is expired."""
        if self.expiry_date:
            return self.expiry_date < timezone.now().date()
        return False
    
    @property
    def days_until_expiry(self):
        """Get days until expiry."""
        if self.expiry_date:
            delta = self.expiry_date - timezone.now().date()
            return delta.days
        return None
    
    def save(self, *args, **kwargs):
        # Auto-update status based on quantity and expiry
        if self.current_quantity <= 0:
            self.status = 'DEPLETED'
        elif self.is_expired:
            self.status = 'EXPIRED'
        elif self.status in ['DEPLETED', 'EXPIRED']:
            self.status = 'AVAILABLE'
        super().save(*args, **kwargs)


class InventoryLedger(TenantModel):
    """
    Append-only ledger for inventory movements.
    All inventory changes are recorded here for complete audit trail.
    """
    TRANSACTION_TYPES = [
        ('IN', 'Stock In'),
        ('OUT', 'Stock Out'),
        ('ADJUST', 'Adjustment'),
        ('TRANSFER_OUT', 'Transfer Out'),
        ('TRANSFER_IN', 'Transfer In'),
        ('SALE', 'Sale'),
        ('RETURN', 'Return'),
        ('DAMAGE', 'Damage/Write-off'),
        ('PRODUCTION', 'Production'),
        ('DISPUTE_REVERSAL', 'Dispute Reversal'),
    ]
    
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='ledger_entries'
    )
    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='ledger_entries'
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='inventory_ledger'
    )
    
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    
    # Quantity: Positive for IN, Negative for OUT
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Positive for stock in, negative for stock out"
    )
    
    # Cost tracking
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Unit cost at time of transaction"
    )
    
    # Reference to source document
    reference_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="Type of source document (Transfer, Sale, etc.)"
    )
    reference_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="ID of source document"
    )
    
    notes = models.TextField(blank=True)
    
    # Audit fields
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='inventory_entries'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Inventory Ledger Entry'
        verbose_name_plural = 'Inventory Ledger'
    
    def __str__(self):
        return f"{self.get_transaction_type_display()}: {self.product.name} x {self.quantity}"
    
    def save(self, *args, **kwargs):
        # Prevent updates after creation (append-only)
        if self.pk:
            raise ValueError("InventoryLedger entries cannot be modified. Create a new adjustment entry instead.")
        
        # Update batch quantity if linked
        if self.batch:
            self.batch.current_quantity += self.quantity
            self.batch.save()
        
        super().save(*args, **kwargs)


class Inventory(TenantModel):
    """
    Denormalized inventory snapshot for efficient syncing.
    Updated by signals or periodic tasks from InventoryLedger.
    """
    product = models.ForeignKey(
        Product, 
        on_delete=models.CASCADE, 
        related_name='inventory_snapshots'
    )
    location = models.ForeignKey(
        Location, 
        on_delete=models.CASCADE, 
        related_name='inventory_snapshots'
    )
    quantity = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0')
    )
    version = models.IntegerField(default=1)
    updated_by_device = models.CharField(max_length=100, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Inventory Snapshots"
        unique_together = ['tenant', 'product', 'location']
        indexes = [
            models.Index(fields=['tenant', 'product', 'location']),
            models.Index(fields=['tenant', 'updated_at']),
        ]

    def __str__(self):
        return f"{self.product.name} @ {self.location.name}: {self.quantity}"


class ShopPrice(TenantModel):
    """
    Shop-specific pricing for products.
    Decouples shop pricing from batch cost.
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='shop_prices'
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='product_prices',
        limit_choices_to={'location_type': 'SHOP'}
    )
    
    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    
    # Margin settings
    min_margin_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        help_text="Minimum margin percentage warning threshold"
    )
    
    # Effective dates
    effective_from = models.DateTimeField(default=timezone.now)
    effective_to = models.DateTimeField(null=True, blank=True)
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-effective_from']
        unique_together = ['product', 'location', 'effective_from']
    
    def __str__(self):
        return f"{self.product.name} @ {self.location.name}: {self.selling_price}"
    
    def get_margin_against_batch(self, batch):
        """Calculate margin against a specific batch cost."""
        if batch.unit_cost and batch.unit_cost > 0:
            margin = ((self.selling_price - batch.unit_cost) / batch.unit_cost) * 100
            return round(margin, 2)
        return None
    
    def check_margin_warning(self):
        """
        Check if current price meets minimum margin against highest batch cost.
        Returns (is_warning, margin_percent, highest_cost)
        """
        highest_cost_batch = Batch.objects.filter(
            product=self.product,
            location=self.location,
            status='AVAILABLE',
            current_quantity__gt=0
        ).order_by('-unit_cost').first()
        
        if highest_cost_batch and highest_cost_batch.unit_cost > 0:
            margin = self.get_margin_against_batch(highest_cost_batch)
            is_warning = margin < self.min_margin_percent
            return is_warning, margin, highest_cost_batch.unit_cost
        
        return False, None, None


class FavoriteProduct(TenantModel):
    """
    Per-location favorite products.
    When a product is favorited at a location, all users at that location
    see it as a favorite (displayed at the top of product lists).
    """
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='favorite_products'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='favorited_at'
    )
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['tenant', 'location', 'product']
        ordering = ['product__name']

    def __str__(self):
        return f"★ {self.product.name} @ {self.location.name}"


class StockAdjustment(TenantModel):
    """
    Stock adjustment request with approval workflow.
    
    - Shop Managers adjusting own location: auto-approved.
    - Stores Managers adjusting another location: PENDING, requires approval.
    - Admin adjusting any location: auto-approved.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending Approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    
    ADJUSTMENT_TYPES = [
        ('ADJUST', 'Adjustment (Add Stock)'),
        ('DAMAGE', 'Damage/Write-off (Remove Stock)'),
    ]
    
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='adjustments'
    )
    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='adjustments'
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name='stock_adjustments'
    )
    
    adjustment_type = models.CharField(max_length=20, choices=ADJUSTMENT_TYPES)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Positive to add, negative to remove"
    )
    reason = models.TextField()
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    # Audit: who requested
    requested_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='adjustment_requests'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Audit: who reviewed
    reviewed_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='adjustment_reviews'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    
    # Link to ledger entry if approved
    ledger_entry = models.ForeignKey(
        InventoryLedger,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='adjustment_source'
    )
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Stock Adjustment'
        verbose_name_plural = 'Stock Adjustments'
    
    def __str__(self):
        return f"{self.get_adjustment_type_display()}: {self.product.name} x {self.quantity} ({self.get_status_display()})"
    
    def approve(self, user, notes=''):
        """Approve the adjustment and create ledger entry."""
        self.status = 'APPROVED'
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        
        # Create the actual inventory ledger entry
        ledger = InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            batch=self.batch,
            location=self.location,
            transaction_type=self.adjustment_type,
            quantity=self.quantity,
            unit_cost=self.batch.unit_cost if self.batch else None,
            notes=f"Adjustment approved by {user.get_full_name() or user.email}. Reason: {self.reason}",
            reference_type='StockAdjustment',
            reference_id=self.pk,
            created_by=self.requested_by,
        )
        self.ledger_entry = ledger
        self.save()
        return ledger
    
    def reject(self, user, notes=''):
        """Reject the adjustment."""
        self.status = 'REJECTED'
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save()


class GoodsReceipt(TenantModel):
    """
    Records a bulk receiving of inventory items.
    """
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PENDING_APPROVAL', 'Pending Approval'),
        ('FINALIZED', 'Finalized'),
    ]
    
    receipt_id = models.CharField(max_length=50) # Auto-generated GR-xxx
    supplier_name = models.CharField(max_length=255, blank=True)
    supplier_reference = models.CharField(max_length=255, blank=True) # e.g. invoice number
    
    location = models.ForeignKey(
        'core.Location',
        on_delete=models.PROTECT,
        related_name='goods_receipts'
    )
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    
    total_expected_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0')
    )
    
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_receipts'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    approved_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_receipts'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['tenant', 'receipt_id']
        
    def __str__(self):
        return f"Goods Receipt {self.receipt_id} - {self.get_status_display()}"
        
    def save(self, *args, **kwargs):
        if not self.receipt_id:
            today = timezone.now().strftime('%Y%m%d')
            last_receipt = GoodsReceipt.objects.filter(
                tenant=self.tenant,
                receipt_id__startswith=f"GR{today}"
            ).order_by('-receipt_id').first()
            if last_receipt:
                try:
                    last_num = int(last_receipt.receipt_id[-4:])
                    self.receipt_id = f"GR{today}{last_num + 1:04d}"
                except ValueError:
                    self.receipt_id = f"GR{today}0001"
            else:
                self.receipt_id = f"GR{today}0001"
        super().save(*args, **kwargs)

    def calculate_total(self):
        self.total_expected_cost = self.items.aggregate(
            total=models.Sum('total_cost')
        )['total'] or Decimal('0')
        self.save(update_fields=['total_expected_cost'])
        
    def finalize(self, user):
        """Finalize the receipt and create batches."""
        if self.status == 'FINALIZED':
            raise ValidationError("Already finalized.")
            
        for item in self.items.all():
            # Create the batch
            batch_number = f"{self.receipt_id}-{item.product.pk}"
            
            # Use expiry date if provided, otherwise default to far future or null
            batch = Batch.objects.create(
                tenant=self.tenant,
                batch_number=batch_number,
                product=item.product,
                location=self.location,
                initial_quantity=item.quantity,
                current_quantity=item.quantity,
                unit_cost=item.unit_cost,
                production_date=item.production_date,
                expiry_date=item.expiry_date,
            )
            
            # Also create inventory ledger entry
            InventoryLedger.objects.create(
                tenant=self.tenant,
                product=item.product,
                batch=batch,
                location=self.location,
                transaction_type='RECEIPT',
                quantity=item.quantity,
                unit_cost=item.unit_cost,
                reference_type='GoodsReceipt',
                reference_id=self.pk,
                notes=f"Received via {self.receipt_id}",
                created_by=user
            )

        self.status = 'FINALIZED'
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save()


class GoodsReceiptItem(TenantModel):
    receipt = models.ForeignKey(
        GoodsReceipt,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='goods_receipt_items'
    )
    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))]
    )
    unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0')
    )
    total_cost = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0')
    )
    
    production_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    
    def save(self, *args, **kwargs):
        self.total_cost = self.quantity * self.unit_cost
        super().save(*args, **kwargs)
        self.receipt.calculate_total()

