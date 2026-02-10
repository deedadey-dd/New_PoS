"""
Sales models for the POS system.
Handles sales, sale items, shifts, and payment tracking.
"""
from django.db import models
from django.db.models import Sum
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.core.exceptions import ValidationError
from decimal import Decimal

from apps.core.models import TenantModel, Location, User
from apps.inventory.models import Product, Batch, InventoryLedger


class ShopSettings(TenantModel):
    """
    Shop-specific settings, including receipt printing configuration.
    """
    PRINTER_CHOICES = [
        ('THERMAL_80MM', '80mm Thermal Printer'),
        ('THERMAL_58MM', '58mm Thermal Printer'),
        ('A4_STANDARD', 'A4 Standard Printer'),
        ('NO_PRINT', 'No Printing (Digital Only)'),
    ]
    
    shop = models.OneToOneField(
        Location,
        on_delete=models.CASCADE,
        related_name='shop_settings',
        limit_choices_to={'location_type': 'SHOP'}
    )
    
    # Receipt settings
    receipt_printer_type = models.CharField(
        max_length=20,
        choices=PRINTER_CHOICES,
        default='THERMAL_80MM'
    )
    receipt_header = models.TextField(
        blank=True,
        help_text="Custom header text for receipts"
    )
    receipt_footer = models.TextField(
        blank=True,
        help_text="Custom footer text for receipts (e.g., 'Thank you!')"
    )
    show_logo_on_receipt = models.BooleanField(default=True)
    
    # Payment settings
    enable_cash_payment = models.BooleanField(default=True)
    enable_credit_payment = models.BooleanField(default=True)
    enable_ecash_payment = models.BooleanField(default=True)
    
    # Paystack settings for E-Cash
    paystack_public_key = models.CharField(max_length=255, blank=True)
    paystack_secret_key = models.CharField(max_length=255, blank=True)
    
    class Meta:
        verbose_name_plural = "Shop Settings"
    
    def __str__(self):
        return f"Settings for {self.shop.name}"


class Shift(TenantModel):
    """
    A work shift for a shop attendant.
    Tracks opening/closing cash and shift timing.
    """
    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('CLOSED', 'Closed'),
    ]
    
    shop = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='shifts',
        limit_choices_to={'location_type': 'SHOP'}
    )
    attendant = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='shifts'
    )
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='OPEN')
    
    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField(null=True, blank=True)
    
    opening_cash = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))]
    )
    closing_cash = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))]
    )
    
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-start_time']
    
    def __str__(self):
        return f"Shift {self.pk} - {self.attendant.get_full_name()} at {self.shop.name}"
    
    @property
    def total_sales(self):
        """Total cash sales during this shift."""
        return self.sales.filter(
            status='COMPLETED',
            payment_method__in=['CASH', 'MIXED']
        ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
    
    @property
    def expected_cash(self):
        """Expected cash = opening + cash sales."""
        return self.opening_cash + self.total_sales
    
    @property
    def cash_variance(self):
        """Difference between expected and actual closing cash."""
        if self.closing_cash is not None:
            return self.closing_cash - self.expected_cash
        return None
    
    def close(self, closing_cash, notes=''):
        """Close the shift."""
        if self.status != 'OPEN':
            raise ValidationError("Shift is already closed.")
        
        self.status = 'CLOSED'
        self.end_time = timezone.now()
        self.closing_cash = closing_cash
        self.notes = notes
        self.save()


class Sale(TenantModel):
    """
    A completed or pending sale transaction.
    """
    PAYMENT_CHOICES = [
        ('CASH', 'Cash'),
        ('CREDIT', 'Credit (Customer Account)'),
        ('ECASH', 'E-Cash (Paystack)'),
        ('MIXED', 'Mixed Payment'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('VOIDED', 'Voided'),
        ('HELD', 'Held'),
    ]
    
    sale_number = models.CharField(max_length=50)
    
    shop = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='sales',
        limit_choices_to={'location_type': 'SHOP'}
    )
    attendant = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='sales'
    )
    shift = models.ForeignKey(
        Shift,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales'
    )
    customer = models.ForeignKey(
        'customers.Customer', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='sales'
    )
    
    payment_method = models.CharField(
        max_length=10,
        choices=PAYMENT_CHOICES,
        default='CASH'
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='PENDING'
    )
    
    # Amounts
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    discount_reason = models.CharField(max_length=255, blank=True)
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    
    # Payment tracking
    amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    change_given = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    
    # E-Cash / Paystack payment reference
    paystack_reference = models.CharField(max_length=100, blank=True)
    paystack_status = models.CharField(max_length=20, blank=True)
    
    # Offline sync fields
    client_sale_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        help_text="Client-generated UUID for offline sale idempotency"
    )
    synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when this offline sale was synced to server"
    )
    offline_created_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Original timestamp when sale was created offline"
    )
    has_sync_conflict = models.BooleanField(
        default=False,
        help_text="Whether this sale had stock or other conflicts during sync"
    )
    sync_conflict_notes = models.TextField(
        blank=True,
        help_text="Details of any sync conflicts"
    )
    
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['tenant', 'sale_number']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'created_at']),
            models.Index(fields=['tenant', 'shop', 'created_at']),
            models.Index(fields=['tenant', 'payment_method']),
            models.Index(fields=['tenant', 'client_sale_id']),
        ]
    
    def __str__(self):
        return f"Sale {self.sale_number} - {self.total}"
    
    def save(self, *args, **kwargs):
        # Auto-generate sale number
        if not self.sale_number:
            today = timezone.now().strftime('%Y%m%d')
            last_sale = Sale.objects.filter(
                tenant=self.tenant,
                sale_number__startswith=f"S{today}"
            ).order_by('-sale_number').first()
            
            if last_sale:
                try:
                    last_num = int(last_sale.sale_number[-4:])
                    self.sale_number = f"S{today}{last_num + 1:04d}"
                except ValueError:
                    self.sale_number = f"S{today}0001"
            else:
                self.sale_number = f"S{today}0001"
        
        super().save(*args, **kwargs)
    
    def calculate_totals(self):
        """Recalculate sale totals from items."""
        self.subtotal = self.items.aggregate(
            total=Sum('total')
        )['total'] or Decimal('0')
        self.total = self.subtotal - self.discount_amount + self.tax_amount
        self.save()
    
    def complete(self, amount_paid, payment_method='CASH', paystack_ref=''):
        """Complete the sale and deduct inventory."""
        if self.status != 'PENDING':
            raise ValidationError(f"Cannot complete sale in {self.status} status.")
        
        self.amount_paid = Decimal(str(amount_paid))
        self.payment_method = payment_method
        
        if payment_method == 'ECASH':
            self.paystack_reference = paystack_ref
            self.paystack_status = 'success'
        
        if self.amount_paid >= self.total:
            self.change_given = self.amount_paid - self.total
        else:
            # Partial payment - credit sale
            if payment_method == 'CASH':
                self.payment_method = 'MIXED'
            elif payment_method == 'CREDIT':
                self.payment_method = 'CREDIT'
                
            # If creating debt, validate customer
            debt_amount = self.total - self.amount_paid
            if debt_amount > 0:
                if not self.customer:
                    # Allow mixed payment without customer? No, debt must be assigned.
                    # But for now let's assume if debt > 0 requires customer
                     raise ValidationError("Customer account required for credit/partial payment.")
                
                # Check credit limit
                if self.customer.credit_limit is not None:
                     if self.customer.current_balance + debt_amount > self.customer.credit_limit:
                         raise ValidationError(f"Credit limit exceeded. Available credit: {self.customer.credit_limit - self.customer.current_balance}")

                # Update customer balance
                from apps.customers.models import CustomerTransaction
                balance_before = self.customer.current_balance
                self.customer.current_balance += debt_amount
                self.customer.save()
                
                # Create transaction record
                CustomerTransaction.objects.create(
                    tenant=self.tenant,
                    customer=self.customer,
                    transaction_type='DEBIT', # Debit = Increase Debt
                    amount=debt_amount,
                    description=f"Credit Purchase (Sale {self.sale_number})",
                    reference_id=self.sale_number,
                    balance_before=balance_before,
                    balance_after=self.customer.current_balance,
                    performed_by=self.attendant
                )

        self.status = 'COMPLETED'
        self.completed_at = timezone.now()
        self.save()
        
        # Deduct inventory and capture cost for profit tracking
        for item in self.items.all():
            # Get the actual unit cost from batch for profit tracking
            actual_cost = Decimal('0')
            if item.batch and item.batch.unit_cost:
                actual_cost = item.batch.unit_cost
            
            # Store the cost on the sale item for profit/loss reporting
            if item.unit_cost == Decimal('0') and actual_cost > 0:
                item.unit_cost = actual_cost
                item.save(update_fields=['unit_cost'])
            
            InventoryLedger.objects.create(
                tenant=self.tenant,
                product=item.product,
                batch=item.batch,
                location=self.shop,
                transaction_type='SALE',
                quantity=-item.quantity,
                unit_cost=actual_cost,  # Use actual cost, not selling price
                reference_type='Sale',
                reference_id=self.pk,
                notes=f"Sale {self.sale_number}",
                created_by=self.attendant
            )
    
    def void(self, reason=''):
        """Void the sale."""
        if self.status == 'VOIDED':
            raise ValidationError("Sale is already voided.")
        
        # If completed, reverse inventory
        if self.status == 'COMPLETED':
            for item in self.items.all():
                InventoryLedger.objects.create(
                    tenant=self.tenant,
                    product=item.product,
                    batch=item.batch,
                    location=self.shop,
                    transaction_type='SALE_VOID',
                    quantity=item.quantity,  # Add back
                    unit_cost=item.unit_price,
                    reference_type='Sale',
                    reference_id=self.pk,
                    notes=f"Void: {reason}" if reason else "Sale voided",
                    created_by=self.attendant
                )
        
        self.status = 'VOIDED'
        self.notes = f"VOIDED: {reason}" if reason else "VOIDED"
        self.save()


class SaleItem(TenantModel):
    """
    Individual item in a sale.
    """
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='sale_items'
    )
    batch = models.ForeignKey(
        Batch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sale_items'
    )
    
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))]
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        help_text="Cost price at time of sale (from batch) for profit tracking"
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    
    class Meta:
        ordering = ['id']
    
    def __str__(self):
        return f"{self.product.name} x {self.quantity}"
    
    def save(self, *args, **kwargs):
        # Calculate line total
        self.total = (self.quantity * self.unit_price) - self.discount_amount
        super().save(*args, **kwargs)
