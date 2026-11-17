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
from django.utils.safestring import mark_safe


class ShopSettings(TenantModel):
    """
    Shop-specific settings, including receipt printing configuration.
    """
    PRINTER_CHOICES = [
        ('THERMAL_80MM', '80mm Thermal Printer'),
        ('THERMAL_58MM', '58mm Thermal Printer'),
        ('A4_STANDARD', 'A4 Standard Printer'),
        ('A5_STANDARD', 'A5 Standard Printer'),
        ('NO_PRINT', 'No Printing (Digital Only)'),
    ]

    FONT_FAMILY_CHOICES = [
        ('CONSOLAS', 'Consolas / Clean Monospace (Crisp on Thermal & Standard)'),
        ('SANSSERIF', 'Modern Sans-Serif (Arial / Segoe UI - Great for A4/A5 & Thermal)'),
        ('LUCIDA', 'Lucida Console / POS Monospace (Wide & Bold Monospace)'),
        ('ROBOTO_MONO', 'Roboto Mono / Clean Tech Monospace'),
        ('COURIER', 'Courier New (Traditional Monospace)'),
        ('MONOSPACE', 'System Default Monospace'),
    ]

    FONT_WEIGHT_CHOICES = [
        ('NORMAL', 'Normal (400 - Recommended for A4/A5 Laser & Inkjet)'),
        ('MEDIUM', 'Medium / Semi-Bold (600 - Recommended for Thermal)'),
        ('BOLD', 'Bold / High Contrast (700 - Darkest Print)'),
    ]

    FONT_SIZE_CHOICES = [
        ('COMPACT', 'Compact (Small - 90%)'),
        ('NORMAL', 'Standard (Normal - 100%)'),
        ('LARGE', 'Large (High Visibility - 110%)'),
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
    receipt_font_family = models.CharField(
        max_length=30,
        choices=FONT_FAMILY_CHOICES,
        default='CONSOLAS',
        help_text="Font family used for printed receipts and waybills."
    )
    receipt_font_weight = models.CharField(
        max_length=20,
        choices=FONT_WEIGHT_CHOICES,
        default='NORMAL',
        help_text="Font darkness/weight for receipt text."
    )
    receipt_font_size = models.CharField(
        max_length=20,
        choices=FONT_SIZE_CHOICES,
        default='NORMAL',
        help_text="Font scale size on receipts."
    )

    hide_zero_stock_in_pos = models.BooleanField(
        default=False,
        help_text="Hide products with zero stock from the POS interface."
    )
    show_items_with_physical_stock_in_pos = models.BooleanField(
        default=False,
        verbose_name="Show products if physical stock exists",
        help_text="In strict sales workflow, show products as long as there is physical stock in inventory, irrespective of undispatched quantities."
    )
    auto_print_receipts = models.BooleanField(
        default=True,
        help_text="Automatically open receipt for printing after completing a sale."
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
    show_customer_info_on_receipt = models.BooleanField(
        default=True,
        help_text="Display customer name and phone number on printed receipts."
    )
    
    # Payment settings
    enable_cash_payment = models.BooleanField(default=True)
    enable_credit_payment = models.BooleanField(default=True)
    enable_ecash_payment = models.BooleanField(default=True)
    enable_momo_payment = models.BooleanField(default=True)
    disallow_manual_momo_amount = models.BooleanField(
        default=True,
        help_text="Disallow manual entry of payment amount for Local Momo (automatically locks to exact sale total)."
    )
    
    # Paystack settings for E-Cash
    paystack_public_key = models.CharField(max_length=255, blank=True)
    paystack_secret_key = models.CharField(max_length=255, blank=True)

    @property
    def receipt_font_family_css(self):
        mapping = {
            'CONSOLAS': 'Consolas, Menlo, "Courier New", monospace',
            'SANSSERIF': 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
            'LUCIDA': '"Lucida Console", "Lucida Sans Typewriter", Monaco, monospace',
            'ROBOTO_MONO': '"Roboto Mono", Consolas, "Courier New", monospace',
            'COURIER': '"Courier New", Courier, monospace',
            'MONOSPACE': 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        }
        return mark_safe(mapping.get(self.receipt_font_family, 'Consolas, Menlo, "Courier New", monospace'))

    @property
    def receipt_font_weight_css(self):
        mapping = {
            'NORMAL': "400",
            'MEDIUM': "600",
            'BOLD': "700",
        }
        return mapping.get(self.receipt_font_weight, "400")

    @property
    def receipt_font_scale_css(self):
        mapping = {
            'COMPACT': "0.92",
            'NORMAL': "1.0",
            'LARGE': "1.1",
        }
        return mapping.get(self.receipt_font_size, "1.0")
    
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
    
    def _disbursed_refunds_qs(self, refund_method=None):
        """Queryset of approved, disbursed RefundRequests for this shift."""
        from apps.sales.models import RefundRequest
        from django.db.models import Q
        time_q = Q(shift__isnull=True, paid_by=self.attendant, paid_at__gte=self.start_time)
        if self.end_time:
            time_q &= Q(paid_at__lte=self.end_time)
        qs = RefundRequest.objects.filter(
            tenant=self.tenant,
            status='APPROVED',
            payout_status='PAID',
        ).filter(Q(shift=self) | time_q)
        if refund_method:
            qs = qs.filter(refund_method=refund_method)
        return qs

    def _collected_sales_qs(self):
        """
        Sales on this shift where payment was actually collected by this shift's attendant/cashier.
        Only includes REFUNDED sales if they were offset by a cash RefundRequest in this shift
        or refunded after this shift had already closed.
        """
        from apps.sales.models import RefundRequest
        from django.db.models import Q
        base_qs = self.sales.filter(
            Q(cashier=self.attendant) | Q(cashier__isnull=True)
        )
        in_shift_cash_refund_sale_ids = set(
            self._disbursed_refunds_qs(refund_method='CASH').values_list('sale_id', flat=True)
        )
        post_shift_refund_sale_ids = set()
        if self.end_time:
            post_shift_refund_sale_ids = set(
                RefundRequest.objects.filter(
                    tenant=self.tenant,
                    sale__shift=self,
                    status='APPROVED',
                    payout_status='PAID',
                    paid_at__gt=self.end_time,
                ).values_list('sale_id', flat=True)
            )
        valid_refunded_ids = in_shift_cash_refund_sale_ids | post_shift_refund_sale_ids
        return base_qs.filter(
            Q(status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']) |
            Q(status='REFUNDED', id__in=valid_refunded_ids)
        )

    @property
    def total_sales(self):
        """Total cash sales collected during this shift (offset by refund_payouts for same-shift cash refunds)."""
        from django.db.models import Q, Case, When, F, Value
        from django.db.models.functions import Coalesce
        sales_qs = self._collected_sales_qs()
        cash_paid_expr = Case(
            When(amount_paid__gt=Decimal('0.00'), then=F('amount_paid') - Coalesce(F('change_given'), Value(Decimal('0.00')))),
            default=F('total')
        )
        cash_total = sales_qs.filter(
            payment_method='CASH'
        ).aggregate(t=Sum(cash_paid_expr))['t'] or Decimal('0')
        mixed_total = sales_qs.filter(
            payment_method='MIXED'
        ).aggregate(t=Sum('amount_paid'))['t'] or Decimal('0')
        # For partial-halt or partial-return refunds, Sale.settle_halted_sale() or execute_payout()
        # already reduced sale.amount_paid in-place by refund_amount. When that partial refund is
        # also disbursed in cash during this shift (subtracted in self.refund_payouts), add back
        # the partial refund amount here so it is not deducted twice from expected_cash.
        partial_addback = self._disbursed_refunds_qs(refund_method='CASH').filter(
            sale__in=sales_qs.filter(payment_method__in=['CASH', 'MIXED'])
        ).filter(
            Q(is_partial_halt=True) | Q(is_partial_return=True)
        ).aggregate(t=Sum('refund_amount'))['t'] or Decimal('0')
        return cash_total + mixed_total + partial_addback
        
    @property
    def payments_on_account(self):
        """Total cash payments received on account during this shift."""
        from apps.customers.models import CustomerTransaction
        from apps.sales.models import Sale
        from django.db.models import Q
        qs = CustomerTransaction.objects.filter(
            tenant=self.tenant,
            performed_by=self.attendant,
            transaction_type='CREDIT',
            created_at__gte=self.start_time
        ).filter(
            Q(description__icontains='(CASH)') | 
            Q(description__icontains='Overpayment') |
            Q(description__icontains='Change from Sale') |
            Q(description='Payment on account')
        ).exclude(
            description__icontains='ECASH'
        ).exclude(
            description__icontains='MOMO'
        )
        if self.end_time:
            qs = qs.filter(created_at__lte=self.end_time)

        # Exclude overpayments/change from sales that were directly refunded without a cash RefundRequest
        excluded_sale_numbers = set(
            self.sales.filter(status='REFUNDED').exclude(
                id__in=self._collected_sales_qs().values_list('id', flat=True)
            ).values_list('sale_number', flat=True)
        )
        if excluded_sale_numbers:
            qs = qs.exclude(reference_id__in=excluded_sale_numbers)
            
        return qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    @property
    def credit_payouts(self):
        """Total cash paid out to customers from their credit balance during this shift."""
        from apps.customers.models import CustomerTransaction
        from django.db.models import Q
        qs = CustomerTransaction.objects.filter(
            tenant=self.tenant,
            performed_by=self.attendant,
            transaction_type='DEBIT',
            created_at__gte=self.start_time
        ).filter(
            Q(description__icontains='Credit payout') | Q(description__icontains='payout to customer')
        )
        if self.end_time:
            qs = qs.filter(created_at__lte=self.end_time)
        return qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    @property
    def refund_payouts(self):
        """Total cash refunds disbursed by this attendant during this shift."""
        return self._disbursed_refunds_qs(refund_method='CASH').aggregate(
            total=Sum('refund_amount')
        )['total'] or Decimal('0.00')

    @property
    def momo_refund_payouts(self):
        """Total momo refunds disbursed by this attendant during this shift."""
        return self._disbursed_refunds_qs(refund_method='MOMO').aggregate(
            total=Sum('refund_amount')
        )['total'] or Decimal('0.00')

    @property
    def total_refunds_disbursed(self):
        """Total of all refunds disbursed during this shift."""
        return self._disbursed_refunds_qs().aggregate(
            total=Sum('refund_amount')
        )['total'] or Decimal('0.00')

    @property
    def expected_cash(self):
        """Expected cash = opening + cash sales + payments on account - credit payouts - cash refunds."""
        return self.opening_cash + self.total_sales + self.payments_on_account - self.credit_payouts - self.refund_payouts
    
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
        ('MOMO', 'Mobile Money'),
        ('MIXED', 'Mixed Payment'),
        ('PENDING_INVOICE', 'Pending Invoice'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PENDING_DISPATCH', 'Pending Dispatch'),
        ('PARTIALLY_DISPATCHED', 'Partially Dispatched'),
        ('COMPLETED', 'Completed'),
        ('VOIDED', 'Voided'),
        ('REFUNDED', 'Refunded'),
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
        max_length=20,
        choices=PAYMENT_CHOICES,
        default='CASH'
    )
    status = models.CharField(
        max_length=20,
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
    paystack_status = models.CharField(
        max_length=20,
        blank=True,
        help_text="Paystack transaction status"
    )
    
    # Accountant Confirmation (for digital payments)
    is_accountant_confirmed = models.BooleanField(
        default=False,
        help_text="True if accountant has confirmed receipt of this digital payment"
    )
    accountant_confirmed_at = models.DateTimeField(null=True, blank=True)
    accountant_confirmed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_sales'
    )
    
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

    # Walk-in / manual customer info (used when no registered customer account)
    customer_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Name entered manually (for walk-in or cashier workflow)"
    )
    customer_phone = models.CharField(
        max_length=30,
        blank=True,
        help_text="Phone number entered manually"
    )

    # Dispatch tracking (Cashier / Strict Sales Workflow)
    is_dispatched = models.BooleanField(
        default=False,
        help_text="Designates whether the goods for this sale have been dispatched by the shop manager."
    )
    dispatched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="The date and time the sale was dispatched."
    )
    dispatched_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dispatched_sales',
        help_text="The user (typically shop manager) who dispatched the goods."
    )
    cashier = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='processed_sales',
        help_text="The cashier who received/processed payment for this sale."
    )
    is_halted = models.BooleanField(
        default=False,
        help_text="Designates whether dispatch was halted by shop manager prior to refund or resumption."
    )
    halt_snapshot = models.JSONField(
        null=True,
        blank=True,
        help_text="Snapshot of sale state and items prior to halt, allowing cashier resumption."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # Sync fields (from main/alpha - for bidirectional sync support)
    client_id = models.CharField(max_length=100, unique=True, null=True, blank=True, db_index=True)
    device_id = models.CharField(max_length=100, null=True, blank=True)
    device_type = models.CharField(
        max_length=20, 
        choices=[('desktop', 'Desktop'), ('tablet', 'Tablet'), ('mobile', 'Mobile')], 
        null=True, 
        blank=True
    )
    created_at_client = models.DateTimeField(null=True, blank=True)
    sync_status = models.CharField(
        max_length=20, 
        choices=[('pending', 'Pending'), ('synced', 'Synced'), ('conflict', 'Conflict'), ('failed', 'Failed')], 
        default='pending'
    )
    retry_count = models.IntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['tenant', 'sale_number']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'created_at']),
            models.Index(fields=['tenant', 'shop', 'created_at']),
            models.Index(fields=['tenant', 'payment_method']),
            models.Index(fields=['tenant', 'client_sale_id']),
            models.Index(fields=['tenant', 'updated_at']),
            models.Index(fields=['tenant', 'shop', 'updated_at']),
            models.Index(fields=['tenant', 'is_dispatched']),
            models.Index(fields=['tenant', 'shop', 'is_dispatched']),
        ]
    
    def __str__(self):
        return f"Sale {self.sale_number} - {self.total}"
    
    def save(self, *args, **kwargs):
        if 'update_fields' in kwargs and kwargs['update_fields'] is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'updated_at'}
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
        
        # Default amount_paid to total for cash sales created directly without amount_paid
        if (self.amount_paid is None or self.amount_paid == Decimal('0.00')) and self.payment_method == 'CASH' and self.total and self.total > Decimal('0.00'):
            self.amount_paid = self.total
        
        super().save(*args, **kwargs)
    
    def calculate_totals(self):
        """Recalculate sale totals from items."""
        self.subtotal = self.items.aggregate(
            total=Sum('total')
        )['total'] or Decimal('0')
        self.total = self.subtotal - self.discount_amount + self.tax_amount
        self.save()

    @property
    def dispatched_total(self):
        """Total value of goods already dispatched."""
        val = Decimal('0.00')
        for item in self.items.all():
            if item.quantity > Decimal('0') and item.dispatched_quantity > Decimal('0'):
                effective_price = item.total / item.quantity
                val += (effective_price * item.dispatched_quantity).quantize(Decimal('0.01'))
        if val <= Decimal('0.00') and self.amount_paid and self.total and self.amount_paid > self.total:
            return self.total
        return val

    @property
    def undispatched_total(self):
        """Value of goods not yet dispatched."""
        disp = self.dispatched_total
        return max(Decimal('0.00'), self.total - disp)

    @property
    def calculated_refund_amount(self):
        """Refund amount due for customer."""
        disp = self.dispatched_total
        if (self.is_halted or self.is_partially_dispatched) and not self.all_items_dispatched:
            if self.amount_paid and self.amount_paid > disp:
                return self.amount_paid - disp
            return Decimal('0.00')
        if self.amount_paid and self.total and self.amount_paid > self.total:
            return self.amount_paid - self.total
        return self.amount_paid or Decimal('0.00')

    @property
    def all_items_dispatched(self):
        """True when every item in this sale has been fully dispatched."""
        if hasattr(self, '_prefetched_objects_cache') and 'items' in self._prefetched_objects_cache:
            return not any(item.dispatched_quantity < item.quantity for item in self.items.all())
        return not self.items.filter(
            dispatched_quantity__lt=models.F('quantity')
        ).exists()

    @property
    def is_partially_dispatched(self):
        """True when a sale has some dispatched quantity and some remaining undispatched quantity."""
        if hasattr(self, '_is_partially_dispatched'):
            return bool(self._is_partially_dispatched)
        if self.status == 'PARTIALLY_DISPATCHED':
            return True
        if self.status != 'PENDING_DISPATCH':
            return False
        if hasattr(self, '_prefetched_objects_cache') and 'items' in self._prefetched_objects_cache:
            has_dispatched = any(item.dispatched_quantity > 0 for item in self.items.all())
            has_undispatched = any(item.dispatched_quantity < item.quantity for item in self.items.all())
            return has_dispatched and has_undispatched
        has_dispatched = self.items.filter(dispatched_quantity__gt=0).exists()
        has_undispatched = self.items.filter(dispatched_quantity__lt=models.F('quantity')).exists()
        return has_dispatched and has_undispatched

    @property
    def has_pending_refund(self):
        """True when this sale has an unapproved refund or halt request."""
        if hasattr(self, '_has_pending_refund'):
            return bool(self._has_pending_refund)
        if hasattr(self, '_prefetched_objects_cache') and 'refund_requests' in self._prefetched_objects_cache:
            return any(r.status == 'PENDING' for r in self.refund_requests.all())
        return self.refund_requests.filter(status='PENDING').exists()

    @property
    def has_pending_halt(self):
        """True when this sale has a pending partial halt request."""
        if hasattr(self, '_has_pending_halt'):
            return bool(self._has_pending_halt)
        if hasattr(self, '_prefetched_objects_cache') and 'refund_requests' in self._prefetched_objects_cache:
            return any(r.status == 'PENDING' and r.is_partial_halt for r in self.refund_requests.all())
        return self.refund_requests.filter(status='PENDING', is_partial_halt=True).exists()

    @property
    def has_approved_pending_payout(self):
        """True when this sale has an approved refund or halt request awaiting cashier payout."""
        if hasattr(self, '_has_approved_pending_payout'):
            return bool(self._has_approved_pending_payout)
        if hasattr(self, '_prefetched_objects_cache') and 'refund_requests' in self._prefetched_objects_cache:
            return any(r.status == 'APPROVED' and r.payout_status == 'PENDING' for r in self.refund_requests.all())
        return self.refund_requests.filter(status='APPROVED', payout_status='PENDING').exists()

    @property
    def has_recent_activity(self):
        """True when the sale had activity (refund, dispatch, halt, update) after its creation."""
        from datetime import timedelta
        latest = getattr(self, 'latest_activity', None)
        if not latest:
            latest = getattr(self, 'updated_at', None)
        if not latest:
            return False
        return latest > (self.created_at + timedelta(seconds=15))

    @property
    def pending_refund_request(self):
        """Return the pending RefundRequest instance, if any."""
        if hasattr(self, '_prefetched_objects_cache') and 'refund_requests' in self._prefetched_objects_cache:
            for r in self.refund_requests.all():
                if r.status == 'PENDING':
                    return r
            return None
        return self.refund_requests.filter(status='PENDING').first()

    @property
    def approved_pending_payout_request(self):
        """Return the approved RefundRequest instance awaiting payout, if any."""
        if hasattr(self, '_prefetched_objects_cache') and 'refund_requests' in self._prefetched_objects_cache:
            for r in self.refund_requests.all():
                if r.status == 'APPROVED' and r.payout_status == 'PENDING':
                    return r
            return None
        return self.refund_requests.filter(status='APPROVED', payout_status='PENDING').first()

    @property
    def can_be_resumed(self):
        """
        True when this sale's dispatch was halted by the shop manager and can be resumed by the cashier.
        Allowed only if halted (is_halted=True or legacy COMPLETED) and no active/approved/disbursed refund or pending refund exists.
        """
        if not self.is_halted:
            if not self.notes or '[Dispatch Resumed' in self.notes:
                return False
            if not ('[Halted & Settled' in self.notes or '[Dispatch Halted' in self.notes):
                return False
            if self.status != 'COMPLETED':
                return False
        if self.status not in ['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED', 'COMPLETED']:
            return False
        if hasattr(self, '_prefetched_objects_cache') and 'refund_requests' in self._prefetched_objects_cache:
            r_list = self.refund_requests.all()
            if any(r.status in ['APPROVED', 'DISBURSED'] or r.payout_status == 'PAID' for r in r_list):
                return False
            if any(r.status == 'PENDING' for r in r_list):
                return False
            return True
        # If any refund request is approved or disbursed, cannot resume
        active_refunds = self.refund_requests.filter(status__in=['APPROVED', 'DISBURSED']).exists() or self.refund_requests.filter(payout_status='PAID').exists()
        if active_refunds:
            return False
        # If pending refund exists, cannot resume until cancelled or rejected
        if self.refund_requests.filter(status='PENDING').exists():
            return False
        return True


    def complete(self, amount_paid, payment_method='CASH', paystack_ref='', cashier=None):
        """Process payment and conditionally complete/dispatch the sale based on workflow."""
        if self.status != 'PENDING':
            raise ValidationError(f"Cannot complete sale in {self.status} status.")
        
        if payment_method == 'CREDIT':
            amount_paid = Decimal('0')
            
        self.amount_paid = Decimal(str(amount_paid))
        self.payment_method = payment_method
        self.cashier = cashier or self.attendant
        if cashier and cashier != self.attendant:
            cashier_shift = Shift.objects.filter(
                tenant=self.tenant,
                attendant=cashier,
                status='OPEN'
            ).first()
            if cashier_shift:
                self.shift = cashier_shift
        
        if payment_method == 'ECASH':
            self.paystack_reference = paystack_ref
            self.paystack_status = 'success'
        
        if self.amount_paid >= self.total:
            self.change_given = self.amount_paid - self.total
            
            # If customer is selected and change is > 0, credit change to account
            if self.customer and self.change_given > 0:
                from apps.customers.models import CustomerTransaction
                balance_before = self.customer.current_balance
                self.customer.current_balance -= self.change_given
                self.customer.save()
                
                CustomerTransaction.objects.create(
                    tenant=self.tenant,
                    customer=self.customer,
                    transaction_type='CREDIT',
                    amount=self.change_given,
                    description=f"Change (CASH)",
                    reference_id=self.sale_number,
                    balance_before=balance_before,
                    balance_after=self.customer.current_balance,
                    performed_by=self.cashier
                )
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
                    description=f"Credit Purchase",
                    reference_id=self.sale_number,
                    balance_before=balance_before,
                    balance_after=self.customer.current_balance,
                    performed_by=self.attendant
                )

        if self.tenant.use_strict_sales_workflow:
            self.status = 'PENDING_DISPATCH'
            self.completed_at = timezone.now()
            self.save()
        else:
            self.status = 'COMPLETED'
            self.completed_at = timezone.now()
            self.is_dispatched = True
            self.dispatched_at = self.completed_at
            self.dispatched_by = self.attendant
            self.save()
            self.deduct_inventory()
            
    def deduct_inventory(self):
        """Deduct inventory and capture cost for profit tracking."""
        for item in self.items.all():
            # Get the actual unit cost from batch for profit tracking
            actual_cost = Decimal('0')
            batch = item.batch
            
            # If no batch linked, find the best available batch at sale location (FEFO)
            if not batch:
                batch = Batch.objects.filter(
                    tenant=self.tenant,
                    product=item.product,
                    location=self.shop,
                    status='AVAILABLE',
                    current_quantity__gt=0,
                ).order_by('expiry_date', 'created_at').first()
                
                # Link the batch to the sale item for future reference
                if batch:
                    item.batch = batch
            
            if batch and batch.unit_cost:
                actual_cost = batch.unit_cost
            
            # Store the cost on the sale item for profit/loss reporting
            if item.unit_cost == Decimal('0') and actual_cost > 0:
                item.unit_cost = actual_cost
                item.save(update_fields=['unit_cost', 'batch'])
            
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
        
        # If completed or pending dispatch, reverse inventory
        if self.status in ['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']:
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

    def refund(self, reason='', user=None):
        """Formally refund the sale. Returns inventory and adjusts balances."""
        if self.status == 'REFUNDED':
            raise ValidationError("Sale is already refunded.")
            
        # 1. Return Inventory (only if it was deducted)
        # In strict workflow, inventory is only deducted if items were actually dispatched. In standard, if completed.
        inventory_deducted = False
        if self.tenant.use_strict_sales_workflow:
            if self.is_dispatched or self.items.filter(dispatched_quantity__gt=0).exists():
                inventory_deducted = True
        else:
            if self.status in ['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']:
                inventory_deducted = True
                
        if inventory_deducted:
            for item in self.items.all():
                if item.dispatched_quantity > Decimal('0'):
                    return_qty = item.dispatched_quantity
                elif not self.tenant.use_strict_sales_workflow:
                    return_qty = item.quantity
                else:
                    return_qty = Decimal('0')
                if return_qty <= Decimal('0'):
                    continue
                InventoryLedger.objects.create(
                    tenant=self.tenant,
                    product=item.product,
                    batch=item.batch,
                    location=self.shop,
                    transaction_type='SALE_RETURN',
                    quantity=return_qty,  # Add back
                    unit_cost=item.unit_price,
                    reference_type='Refund',
                    reference_id=self.pk,
                    notes=f"Refund: {reason}" if reason else "Sale refunded",
                    created_by=user or self.attendant
                )

            # Reset dispatch tracking on items so refunded quantities
            # don't inflate dispatched-goods counts in end-of-day reports.
            self.items.all().update(dispatched_quantity=Decimal('0'))

        # Clear sale-level dispatch flags so the sale no longer appears
        # as dispatched (goods have been returned).
        if self.is_dispatched or self.dispatched_at:
            self.is_dispatched = False
            self.dispatched_at = None
            self.dispatched_by = None
        # 2. Reverse Customer Transactions (if credit or partial payment)
        if self.customer:
            debt_amount = self.total - self.amount_paid
            if self.payment_method == 'PENDING_INVOICE':
                debt_amount = self.total
                
            if debt_amount > 0:
                from apps.customers.models import CustomerTransaction
                balance_before = self.customer.current_balance
                self.customer.current_balance -= debt_amount
                self.customer.save()
                
                CustomerTransaction.objects.create(
                    tenant=self.tenant,
                    customer=self.customer,
                    transaction_type='CREDIT',
                    amount=debt_amount,
                    reference_id=self.sale_number,
                    balance_before=balance_before,
                    balance_after=self.customer.current_balance,
                    description=f"Refund: {reason}",
                    performed_by=user or self.attendant
                )
            
        self.status = 'REFUNDED'
        self.is_halted = False
        self.halt_snapshot = None
        self.notes = f"REFUNDED: {reason}" if reason else "REFUNDED"
        self.save()

    def halt_dispatch(self, user=None, reason=''):
        """
        Halt an ongoing dispatch in the Strict Sales Workflow without deleting items.
        Locks dispatching and allows the cashier to request a refund for undispatched goods.
        Sale remains in PENDING_DISPATCH with is_halted=True and all line items intact.
        """
        if self.status not in ['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']:
            raise ValidationError(f"Cannot halt dispatch for sale in {self.status} status.")

        self.is_halted = True
        reason_suffix = f": {reason}" if reason else ""
        halt_note = f"[Dispatch Halted{reason_suffix}]"
        self.notes = f"{self.notes}\n{halt_note}".strip() if self.notes else halt_note
        self.save(update_fields=['is_halted', 'notes'])

    def settle_halted_sale(self, user=None, refund_req=None, refund_method='CASH', reason=''):
        """
        Officially settle a halted sale upon approval/payout of the refund request.
        - If partially dispatched: truncates undispatched items to match actual dispatched quantities.
        - If zero items dispatched: keeps original items intact so full sale details remain for cashier refund.
        Undispatched quantities are cancelled; no inventory return is created because
        undispatched goods were never deducted.
        """
        from django.db import transaction as db_transaction
        from django.utils import timezone as tz

        with db_transaction.atomic():
            pre_subtotal = self.subtotal
            pre_discount = self.discount_amount
            pre_discount_reason = self.discount_reason
            pre_tax = self.tax_amount
            pre_total = self.total
            pre_paid = self.amount_paid
            old_debt = max(Decimal('0.00'), pre_total - pre_paid)

            items_snapshot = [
                {
                    'product_id': item.product_id,
                    'batch_id': item.batch_id,
                    'quantity': str(item.quantity),
                    'dispatched_quantity': str(item.dispatched_quantity),
                    'unit_price': str(item.unit_price),
                    'unit_cost': str(item.unit_cost),
                    'discount_amount': str(item.discount_amount),
                    'total': str(item.total),
                }
                for item in self.items.all()
            ]

            has_dispatched_items = self.items.filter(dispatched_quantity__gt=0).exists()

            if has_dispatched_items:
                # 1. Truncate items to actual dispatched quantities
                for item in list(self.items.all()):
                    if item.dispatched_quantity <= Decimal('0'):
                        item.delete()
                    elif item.dispatched_quantity < item.quantity:
                        item.quantity = item.dispatched_quantity
                        item.save()

                # 2. Recalculate totals from surviving items
                self.calculate_totals()
            else:
                # Zero items dispatched: keep original items intact
                pass

            # 3. Adjust amount_paid if cash/fund refund is given
            refund_amt = refund_req.refund_amount if refund_req else Decimal('0.00')
            if refund_req:
                debt_reduction = refund_req.debt_reduction_amount
            else:
                new_debt = max(Decimal('0.00'), self.total - self.amount_paid)
                debt_reduction = max(Decimal('0.00'), old_debt - new_debt)

            # 3. Adjust amount_paid only if payout is already paid, account credit, or direct non-request settlement
            should_reduce_paid = False
            if refund_req:
                if refund_req.payout_status == 'PAID' or refund_method == 'ACCOUNT_CREDIT':
                    should_reduce_paid = True
            else:
                should_reduce_paid = True

            if should_reduce_paid and refund_amt > Decimal('0.00'):
                self.amount_paid = max(Decimal('0.00'), self.amount_paid - refund_amt)

            # 4. Finalize sale status to COMPLETED and mark dispatched
            self.status = 'COMPLETED'
            self.is_dispatched = True
            self.dispatched_at = tz.now()
            self.dispatched_by = user or self.dispatched_by
            self.is_halted = False
            self.halt_snapshot = {
                'subtotal': str(pre_subtotal),
                'discount_amount': str(pre_discount),
                'discount_reason': pre_discount_reason,
                'tax_amount': str(pre_tax),
                'total': str(pre_total),
                'amount_paid': str(pre_paid),
                'debt_reduction': str(debt_reduction),
                'items': items_snapshot,
                'halt_reason': reason,
                'halted_by_id': user.id if user else None,
            }
            if refund_req:
                halt_note = f"[Halted & Settled via {refund_req.refund_number}]"
            else:
                reason_suffix = f": {reason}" if reason else ""
                if has_dispatched_items:
                    halt_note = f"[Halted & Settled{reason_suffix}]"
                else:
                    halt_note = f"[Dispatch Halted (No Goods Released){reason_suffix}]"
            self.notes = f"{self.notes}\n{halt_note}".strip() if self.notes else halt_note
            self.save()

            # 5. Customer balance adjustment if registered customer
            if self.customer:
                from apps.customers.models import CustomerTransaction
                # A. Debt reduction (cancelling credit debt for undispatched goods)
                if debt_reduction > Decimal('0.00'):
                    balance_before = self.customer.current_balance
                    self.customer.current_balance -= debt_reduction
                    self.customer.save()

                    CustomerTransaction.objects.create(
                        tenant=self.tenant,
                        customer=self.customer,
                        transaction_type='CREDIT',
                        amount=debt_reduction,
                        reference_id=self.sale_number,
                        balance_before=balance_before,
                        balance_after=self.customer.current_balance,
                        description=f"Debt reduction for halted undispatched items ({self.sale_number})",
                        performed_by=user or self.attendant
                    )

                # B. If refund_method is ACCOUNT_CREDIT, credit the refund amount to customer account
                if refund_method == 'ACCOUNT_CREDIT' and refund_amt > Decimal('0.00'):
                    balance_before = self.customer.current_balance
                    self.customer.current_balance -= refund_amt
                    self.customer.save()

                    CustomerTransaction.objects.create(
                        tenant=self.tenant,
                        customer=self.customer,
                        transaction_type='CREDIT',
                        amount=refund_amt,
                        reference_id=self.sale_number,
                        balance_before=balance_before,
                        balance_after=self.customer.current_balance,
                        description=f"Account credit refund for halted undispatched items ({self.sale_number})",
                        performed_by=user or self.attendant
                    )

    def halt_and_settle(self, user=None, refund_req=None, refund_method='CASH', reason=''):
        """
        Backwards-compatible wrapper.
        If called with refund_req (e.g. during refund approval/payout), settles the sale.
        If called without refund_req (e.g. initial manager halt), halts dispatch without deleting items.
        """
        if refund_req is not None:
            return self.settle_halted_sale(user=user, refund_req=refund_req, refund_method=refund_method, reason=reason)
        return self.halt_dispatch(user=user, reason=reason)

    def resume_dispatch(self, user=None, reason=''):
        """
        Resume a halted sale in the Strict Sales Workflow, returning it to PENDING_DISPATCH
        and restoring its original pre-halt items and quantities.
        Only allowed if the sale was halted and no refund has been approved or paid out.
        """
        from django.db import transaction as db_transaction
        from decimal import Decimal

        if not self.is_halted and not (self.notes and ('[Halted & Settled' in self.notes or '[Dispatch Halted' in self.notes)):
            raise ValidationError("This sale is not in a halted state and cannot be resumed.")

        # Check for active refunds
        active_refunds = self.refund_requests.filter(status__in=['APPROVED', 'DISBURSED']).exists() or self.refund_requests.filter(payout_status='PAID').exists()
        if active_refunds:
            raise ValidationError("Cannot resume transaction: a refund has already been approved or disbursed for this sale.")

        pending_refunds = self.refund_requests.filter(status='PENDING')
        if pending_refunds.exists():
            raise ValidationError("A refund request is currently pending approval for this sale. Please cancel the refund request before resuming.")

        with db_transaction.atomic():
            snapshot = self.halt_snapshot or {}

            # 1. Reverse debt reduction if customer had one
            debt_reduction_str = snapshot.get('debt_reduction', '0.00')
            try:
                debt_reduction = Decimal(str(debt_reduction_str))
            except Exception:
                debt_reduction = Decimal('0.00')

            if self.customer and debt_reduction > Decimal('0.00'):
                from apps.customers.models import CustomerTransaction
                balance_before = self.customer.current_balance
                self.customer.current_balance += debt_reduction
                self.customer.save()

                CustomerTransaction.objects.create(
                    tenant=self.tenant,
                    customer=self.customer,
                    transaction_type='DEBIT',
                    amount=debt_reduction,
                    reference_id=self.sale_number,
                    balance_before=balance_before,
                    balance_after=self.customer.current_balance,
                    description=f"Reversed debt reduction on dispatch resumption ({self.sale_number})",
                    performed_by=user or self.attendant
                )

            # 2. Restore items from snapshot if available
            items_data = snapshot.get('items')
            if items_data:
                self.items.all().delete()
                for item_dict in items_data:
                    SaleItem.objects.create(
                        tenant=self.tenant,
                        sale=self,
                        product_id=item_dict['product_id'],
                        batch_id=item_dict.get('batch_id'),
                        quantity=Decimal(str(item_dict['quantity'])),
                        dispatched_quantity=Decimal(str(item_dict.get('dispatched_quantity', '0'))),
                        unit_price=Decimal(str(item_dict['unit_price'])),
                        unit_cost=Decimal(str(item_dict.get('unit_cost', '0'))),
                        discount_amount=Decimal(str(item_dict.get('discount_amount', '0'))),
                        total=Decimal(str(item_dict['total'])),
                    )
                self.subtotal = Decimal(str(snapshot.get('subtotal', self.subtotal)))
                self.discount_amount = Decimal(str(snapshot.get('discount_amount', self.discount_amount)))
                self.discount_reason = snapshot.get('discount_reason', self.discount_reason)
                self.tax_amount = Decimal(str(snapshot.get('tax_amount', self.tax_amount)))
                self.total = Decimal(str(snapshot.get('total', self.total)))
                self.amount_paid = Decimal(str(snapshot.get('amount_paid', self.amount_paid)))
            else:
                self.calculate_totals()

            # 3. Reset sale status back to PENDING_DISPATCH or PARTIALLY_DISPATCHED
            has_dispatched = self.items.filter(dispatched_quantity__gt=0).exists()
            self.status = 'PARTIALLY_DISPATCHED' if has_dispatched else 'PENDING_DISPATCH'
            self.is_dispatched = False
            self.dispatched_at = None
            self.dispatched_by = None
            self.is_halted = False
            self.halt_snapshot = None

            reason_str = f": {reason}" if reason else ""
            resumed_note = f"[Dispatch Resumed by {user.get_full_name() if user else 'Cashier'}{reason_str}]"
            self.notes = f"{self.notes}\n{resumed_note}".strip() if self.notes else resumed_note
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
    dispatched_quantity = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0'),
        help_text="Quantity that has already been dispatched to the customer (for partial dispatch)."
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

    @property
    def remaining_quantity(self):
        """Quantity not yet dispatched to the customer."""
        return self.quantity - self.dispatched_quantity

    @property
    def is_fully_dispatched(self):
        """True when all units of this item have been dispatched."""
        return self.dispatched_quantity >= self.quantity
    
    class Meta:
        ordering = ['id']
    
    def __str__(self):
        return f"{self.product.name} x {self.quantity}"
    
    def save(self, *args, **kwargs):
        # Calculate line total
        self.total = (self.quantity * self.unit_price) - self.discount_amount
        super().save(*args, **kwargs)
        if self.sale_id:
            try:
                Sale.objects.filter(pk=self.sale_id).update(updated_at=timezone.now())
            except Exception:
                pass


class RefundRequest(TenantModel):
    """
    A refund request submitted by a Shop Manager for a completed sale.
    Requires approval from an Accountant or Admin before the refund is executed.

    Financial impact on approval (controlled by tenant.refund_always_cash):
      - refund_always_cash=True  → CashTransfer(EXPENDITURE) deducted from shop cash-on-hand
      - refund_always_cash=False → mirrors original payment:
          CASH/MIXED → CashTransfer(EXPENDITURE)
          ECASH      → DigitalFundWithdrawal reversal (negative ECASH entry)
          MOMO       → DigitalFundWithdrawal reversal (negative MOMO entry)
          CREDIT     → CustomerTransaction reversal (handled by Sale.refund())
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending Approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled'),
    ]
    REFUND_METHOD_CHOICES = [
        ('CASH', 'Cashier Till (Cash)'),
        ('MOMO', 'Mobile Money'),
        ('ECASH', 'E-Cash'),
        ('ACCOUNT_CREDIT', 'Customer Account Credit'),
    ]
    PAYOUT_STATUS_CHOICES = [
        ('PENDING', 'Pending Payout'),
        ('PAID', 'Paid / Completed'),
    ]

    refund_number = models.CharField(max_length=30, blank=True)
    sale = models.ForeignKey(
        Sale,
        on_delete=models.PROTECT,
        related_name='refund_requests'
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='refund_requests_made'
    )
    reason = models.TextField(help_text="Reason for the refund")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')

    is_partial_halt = models.BooleanField(
        default=False,
        help_text="True if this is a halt/settlement of undispatched goods in strict workflow"
    )
    is_partial_return = models.BooleanField(
        default=False,
        help_text="True if this is a partial return of dispatched goods after dispatch"
    )
    returned_items = models.JSONField(
        null=True,
        blank=True,
        help_text="Snapshot of returned items for partial returns"
    )
    refund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Amount of money/funds to be returned to customer"
    )
    debt_reduction_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Amount by which customer credit debt is reduced"
    )
    dispatched_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Total value of goods dispatched before halt"
    )
    refund_method = models.CharField(
        max_length=20,
        blank=True,
        choices=REFUND_METHOD_CHOICES,
        default='CASH',
        help_text="Method selected by Accountant to pay the refund"
    )
    payout_status = models.CharField(
        max_length=20,
        choices=PAYOUT_STATUS_CHOICES,
        default='PENDING',
        help_text="Payout status (PENDING if cashier needs to dispense funds, PAID once completed)"
    )
    paid_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='refunds_paid_out',
        help_text="Cashier or accountant who executed the payout"
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    shift = models.ForeignKey(
        'sales.Shift',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='refund_requests_paid',
        help_text="Cashier shift during which this refund was disbursed"
    )
    payout_notes = models.TextField(
        blank=True,
        help_text="Notes, transaction reference, or receipt number recorded upon disbursement"
    )

    reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='refund_requests_reviewed'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Refund Request"
        verbose_name_plural = "Refund Requests"

    def __str__(self):
        return f"Refund {self.refund_number or self.pk} — Sale {self.sale.sale_number} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if not self.refund_number:
            today = timezone.now().strftime('%Y%m%d')
            last = RefundRequest.objects.filter(
                tenant=self.tenant,
                refund_number__startswith=f"RFD{today}"
            ).order_by('-refund_number').first()
            if last and last.refund_number:
                try:
                    num = int(last.refund_number[-4:]) + 1
                except ValueError:
                    num = 1
            else:
                num = 1
            self.refund_number = f"RFD{today}{num:04d}"
        if not self.refund_amount or self.refund_amount <= Decimal('0.00'):
            if self.sale_id and hasattr(self, 'sale') and self.sale:
                if self.sale.amount_paid and self.sale.total and self.sale.amount_paid > self.sale.total:
                    self.is_partial_halt = True
                    self.refund_amount = self.sale.amount_paid - self.sale.total
                    self.dispatched_total = self.sale.total
                elif not self.is_partial_halt and not self.is_partial_return:
                    self.refund_amount = self.sale.amount_paid if (self.sale.amount_paid and self.sale.amount_paid > Decimal('0.00')) else self.sale.total
                    self.dispatched_total = Decimal('0.00')
        if not self.is_partial_halt and not self.is_partial_return:
            self.dispatched_total = Decimal('0.00')
        if self.is_partial_halt and (not self.dispatched_total or self.dispatched_total <= Decimal('0.00')):
            if self.sale_id and hasattr(self, 'sale') and self.sale:
                if self.sale.total and self.sale.total > Decimal('0.00'):
                    self.dispatched_total = self.sale.total
                elif self.sale.amount_paid and self.refund_amount and self.sale.amount_paid > self.refund_amount:
                    self.dispatched_total = self.sale.amount_paid - self.refund_amount
        super().save(*args, **kwargs)
        if self.sale_id:
            try:
                Sale.objects.filter(pk=self.sale_id).update(updated_at=timezone.now())
            except Exception:
                pass

    @property
    def effective_dispatched_total(self):
        """
        Return the value of goods dispatched (kept by customer).
        Provides automatic calculation for legacy or existing refund requests
        where dispatched_total was saved as 0.00.
        """
        if not self.is_partial_halt and not self.is_partial_return:
            return Decimal('0.00')
        if self.dispatched_total and self.dispatched_total > Decimal('0.00'):
            return self.dispatched_total
        if self.is_partial_halt:
            if self.sale:
                if hasattr(self.sale, 'dispatched_total') and self.sale.dispatched_total > Decimal('0.00'):
                    return self.sale.dispatched_total
                if self.sale.halt_snapshot and self.sale.halt_snapshot.get('dispatched_total'):
                    try:
                        return Decimal(str(self.sale.halt_snapshot['dispatched_total']))
                    except Exception:
                        pass
                ref_amt = self.refund_amount or Decimal('0.00')
                debt_amt = self.debt_reduction_amount or Decimal('0.00')
                total_ded = ref_amt + debt_amt
                if self.sale.amount_paid and self.sale.amount_paid > total_ded:
                    return self.sale.amount_paid - total_ded
                if self.sale.total and self.sale.total > total_ded:
                    return self.sale.total - total_ded
                if self.sale.notes and ('[Halted & Settled' in self.sale.notes or '[Dispatch Halted' in self.sale.notes):
                    return self.sale.total
        return Decimal('0.00')

    @property
    def undispatched_total(self):
        """
        Return the value of undispatched items cancelled in partial halt.
        """
        if self.is_partial_halt:
            ref_amt = self.refund_amount or Decimal('0.00')
            debt_amt = self.debt_reduction_amount or Decimal('0.00')
            calc_undisp = ref_amt + debt_amt
            if calc_undisp > Decimal('0.00'):
                return calc_undisp
            orig = self.original_sale_total
            disp = self.effective_dispatched_total
            if orig > disp:
                return orig - disp
        return Decimal('0.00')

    @property
    def original_sale_total(self):
        """
        Return the pre-halt original sale total for this refund request.
        """
        if self.is_partial_halt:
            if self.sale and self.sale.halt_snapshot and self.sale.halt_snapshot.get('total'):
                try:
                    return Decimal(str(self.sale.halt_snapshot['total']))
                except Exception:
                    pass
            eff_disp = self.effective_dispatched_total
            ref_amt = self.refund_amount or Decimal('0.00')
            debt_amt = self.debt_reduction_amount or Decimal('0.00')
            calc_total = eff_disp + ref_amt + debt_amt
            if calc_total > Decimal('0.00'):
                return calc_total
            if self.sale and self.sale.amount_paid and self.sale.amount_paid > Decimal('0.00'):
                return self.sale.amount_paid
        if self.sale:
            return self.sale.total
        return Decimal('0.00')

    @property
    def effective_amount_paid(self):
        """
        Return the pre-halt amount paid for this refund request.
        For legacy sales where sale.amount_paid was overwritten with dispatched_total,
        reconstructs the true paid amount as dispatched_total + refund_amount.
        """
        if not self.sale:
            return Decimal('0.00')
        if self.is_partial_halt:
            if self.sale.halt_snapshot and self.sale.halt_snapshot.get('amount_paid'):
                try:
                    return Decimal(str(self.sale.halt_snapshot['amount_paid']))
                except Exception:
                    pass
            ref_amt = self.refund_amount or Decimal('0.00')
            disp_total = self.effective_dispatched_total
            reconstructed_paid = disp_total + ref_amt
            curr_paid = self.sale.amount_paid or Decimal('0.00')
            if curr_paid < reconstructed_paid:
                return reconstructed_paid
            return curr_paid
        if self.is_partial_return:
            ref_amt = self.refund_amount or Decimal('0.00')
            curr_paid = self.sale.amount_paid or Decimal('0.00')
            if self.payout_status == 'PAID':
                return curr_paid + ref_amt
            return curr_paid
        return self.sale.amount_paid or Decimal('0.00')

    def approve(self, approver, refund_method=None):
        """
        Approve the refund request.
        For partial halt in strict workflow: settles the sale to dispatched items and handles refund method.
        For full refund: executes Sale.refund() and applies financial adjustment.
        """
        from django.db import transaction as db_transaction
        from django.utils import timezone as tz

        if self.status != 'PENDING':
            raise ValidationError("Only pending refund requests can be approved.")

        with db_transaction.atomic():
            sale = self.sale
            tenant = self.tenant

            if refund_method:
                method = refund_method
            elif self.refund_method and self.refund_method != 'CASH':
                method = self.refund_method
            else:
                method = 'CASH' if tenant.refund_always_cash else sale.payment_method
            self.refund_method = method

            if not self.is_partial_halt and (self.refund_amount is None or self.refund_amount <= Decimal('0.00')):
                if sale.amount_paid and sale.amount_paid > Decimal('0.00'):
                    self.refund_amount = sale.amount_paid
                elif sale.payment_method != 'CREDIT' and sale.total > Decimal('0.00'):
                    self.refund_amount = sale.total

            if method == 'ACCOUNT_CREDIT' or self.refund_amount <= Decimal('0.00'):
                # Customer account credit / debt cancellation only - no physical cashier funds leave shop
                if self.is_partial_return:
                    from apps.inventory.models import InventoryLedger
                    returned_data = self.returned_items or []
                    for ret_it in returned_data:
                        item_id = ret_it.get('sale_item_id')
                        ret_qty = Decimal(str(ret_it.get('return_qty', '0')))
                        if item_id and ret_qty > Decimal('0'):
                            sale_item = sale.items.filter(pk=item_id).first()
                            if sale_item:
                                actual_cost = Decimal(str(ret_it.get('unit_price', sale_item.unit_price)))
                                InventoryLedger.objects.create(
                                    tenant=tenant,
                                    product=sale_item.product,
                                    batch=sale_item.batch,
                                    location=sale.shop,
                                    transaction_type='SALE_RETURN',
                                    quantity=ret_qty,
                                    unit_cost=actual_cost,
                                    reference_type='Refund',
                                    reference_id=self.pk,
                                    notes=f"Sale return ({self.refund_number}): {ret_qty}x {sale_item.product.name}",
                                    created_by=approver
                                )
                                sale_item.dispatched_quantity = max(Decimal('0'), sale_item.dispatched_quantity - ret_qty)
                                sale_item.save(update_fields=['dispatched_quantity'])

                    if self.debt_reduction_amount > Decimal('0') and sale.customer:
                        from apps.customers.models import CustomerTransaction
                        balance_before = sale.customer.current_balance
                        sale.customer.current_balance -= self.debt_reduction_amount
                        sale.customer.save(update_fields=['current_balance'])
                        CustomerTransaction.objects.create(
                            tenant=tenant,
                            customer=sale.customer,
                            transaction_type='CREDIT',
                            amount=self.debt_reduction_amount,
                            reference_id=sale.sale_number,
                            balance_before=balance_before,
                            balance_after=sale.customer.current_balance,
                            description=f"Debt cancellation from partial return ({self.refund_number})",
                            performed_by=approver
                        )

                    if self.refund_amount > Decimal('0'):
                        sale.amount_paid = max(Decimal('0.00'), sale.amount_paid - self.refund_amount)
                        p_note = f"[Partial return of {tenant.currency_symbol}{self.refund_amount} credited to customer via {self.refund_number}]"
                        sale.notes = f"{sale.notes}\n{p_note}".strip() if sale.notes else p_note
                        sale.save()
                        if sale.customer:
                            from apps.customers.models import CustomerTransaction
                            balance_before = sale.customer.current_balance
                            sale.customer.current_balance -= self.refund_amount
                            sale.customer.save(update_fields=['current_balance'])
                            CustomerTransaction.objects.create(
                                tenant=tenant,
                                customer=sale.customer,
                                transaction_type='CREDIT',
                                amount=self.refund_amount,
                                reference_id=sale.sale_number,
                                balance_before=balance_before,
                                balance_after=sale.customer.current_balance,
                                description=f"Account credit refund ({sale.sale_number})",
                                performed_by=approver
                            )
                    self.payout_status = 'PAID'
                    self.paid_by = approver
                    self.paid_at = tz.now()
                elif self.is_partial_halt:
                    if sale.status in ['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']:
                        sale.halt_and_settle(user=approver, refund_req=self, refund_method=method)
                    else:
                        if self.refund_amount > Decimal('0.00'):
                            sale.amount_paid = max(Decimal('0.00'), sale.amount_paid - self.refund_amount)
                            p_note = f"[Halted & Settled via {self.refund_number}]"
                            sale.notes = f"{sale.notes}\n{p_note}".strip() if sale.notes else p_note
                            sale.save()
                        if sale.customer and self.refund_amount > Decimal('0.00'):
                            from apps.customers.models import CustomerTransaction
                            balance_before = sale.customer.current_balance
                            sale.customer.current_balance -= self.refund_amount
                            sale.customer.save()
                            CustomerTransaction.objects.create(
                                tenant=tenant,
                                customer=sale.customer,
                                transaction_type='CREDIT',
                                amount=self.refund_amount,
                                reference_id=sale.sale_number,
                                balance_before=balance_before,
                                balance_after=sale.customer.current_balance,
                                description=f"Account credit refund ({sale.sale_number})",
                                performed_by=approver
                            )
                    orig_pay_method = sale.payment_method
                    if orig_pay_method == 'MOMO' and self.refund_amount > Decimal('0.00'):
                        from apps.accounting.models import DigitalFundWithdrawal
                        DigitalFundWithdrawal.objects.create(
                            tenant=tenant,
                            shop=sale.shop,
                            accountant=approver,
                            amount=-abs(self.refund_amount),
                            fund_source='MOMO',
                            notes=f"Refund reversal (Store credit): Sale {sale.sale_number} [{self.refund_number}]"
                        )
                else:
                    sale.refund(reason=self.reason, user=approver)
                    self._apply_financial_adjustment(approver)

                self.payout_status = 'PAID'
                self.paid_by = approver
                self.paid_at = tz.now()
                sale.is_halted = False
                sale.halt_snapshot = None
                sale.save(update_fields=['is_halted', 'halt_snapshot'])
            else:
                # CASH, MOMO, ECASH: Approved by accountant, awaiting cashier payout disbursement.
                # Settle the sale to actual dispatched items upon approval.
                if self.is_partial_halt and sale.status in ['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']:
                    sale.settle_halted_sale(user=approver, refund_req=self, refund_method=method)
                # Crucial: NO physical fund deductions occur upon approval (only upon payout).
                self.payout_status = 'PENDING'
                self.paid_by = None
                self.paid_at = None

            # Mark request approved
            self.status = 'APPROVED'
            self.reviewed_by = approver
            self.reviewed_at = tz.now()
            self.save()

        # Notify the shop manager who submitted the request, cashier and accountants via in-app & bulletin
        from apps.notifications.services import notify_and_bulletin
        refund_type_str = "Partial Return" if self.is_partial_return else ("Partial Refund & Settle" if self.is_partial_halt else "Refund")
        amt_display = f"{tenant.currency_symbol}{self.refund_amount}" if (self.is_partial_halt or self.is_partial_return) else f"{tenant.currency_symbol}{sale.total}"
        notify_and_bulletin(
            tenant=tenant,
            title=f"{refund_type_str} Approved: {self.refund_number}",
            message=(
                f"Refund request {self.refund_number} for Sale {sale.sale_number} ({amt_display}) "
                f"at {sale.shop.name} has been approved by {approver.get_full_name() or approver.email}. "
                f"Ready for cashier payout/disbursement. Reason: {self.reason}"
            ),
            notification_type='REFUND_REQUEST',
            target_roles=['SHOP_CASHIER', 'SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN'],
            target_users=[self.requested_by] if self.requested_by else [],
            reference_type='RefundRequest',
            reference_id=self.pk,
            created_by=approver,
            exclude_users=[approver],
            is_pinned=True
        )

    @classmethod
    def channel_refunds(cls, tenant, channel, shop=None, date_from=None, date_to=None):
        """
        Disbursed refunds relevant to a payment channel's history page ('CASH', 'MOMO' or 'ECASH').
        A refund is relevant when it was paid out through the channel OR when the original sale
        was paid through the channel (even if the refund was paid via another channel).
        Informational: refunded sales drop out of channel balances, so these rows keep the
        refund visible in history without double-counting.
        """
        from django.db.models import Q
        sale_methods = {
            'CASH': ['CASH', 'MIXED'],
            'MOMO': ['MOMO', 'LOCAL_MOMO'],
            'ECASH': ['ECASH'],
        }[channel]
        qs = cls.objects.filter(
            tenant=tenant, status='APPROVED', payout_status='PAID'
        ).filter(
            Q(refund_method=channel) | Q(sale__payment_method__in=sale_methods)
        ).select_related('sale', 'sale__shop', 'sale__customer', 'paid_by')
        if shop:
            qs = qs.filter(sale__shop=shop)
        if date_from:
            qs = qs.filter(paid_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(paid_at__date__lte=date_to)
        return qs.order_by('-paid_at')

    def execute_payout(self, disburser, shift=None, transaction_reference='', payout_notes=''):
        """
        Cashier or Admin dispenses the payout funds (CASH, MOMO, ECASH).
        This effectively executes the deductions from the various fund sources
        and finalizes the sale settlement / refund.
        """
        from django.db import transaction as db_transaction
        from django.utils import timezone as tz

        if self.status != 'APPROVED':
            raise ValidationError("Refund request must be approved before payout.")
        if self.payout_status == 'PAID':
            raise ValidationError("This refund has already been paid out.")

        with db_transaction.atomic():
            sale = self.sale
            tenant = self.tenant
            method = self.refund_method

            # 1. Execute the financial adjustments and sale status settlement
            if self.is_partial_return:
                from apps.inventory.models import InventoryLedger
                returned_data = self.returned_items or []
                for ret_it in returned_data:
                    item_id = ret_it.get('sale_item_id')
                    ret_qty = Decimal(str(ret_it.get('return_qty', '0')))
                    if item_id and ret_qty > Decimal('0'):
                        sale_item = sale.items.filter(pk=item_id).first()
                        if sale_item:
                            actual_cost = Decimal(str(ret_it.get('unit_price', sale_item.unit_price)))
                            InventoryLedger.objects.create(
                                tenant=tenant,
                                product=sale_item.product,
                                batch=sale_item.batch,
                                location=sale.shop,
                                transaction_type='SALE_RETURN',
                                quantity=ret_qty,
                                unit_cost=actual_cost,
                                reference_type='Refund',
                                reference_id=self.pk,
                                notes=f"Sale return ({self.refund_number}): {ret_qty}x {sale_item.product.name}",
                                created_by=disburser
                            )
                            sale_item.dispatched_quantity = max(Decimal('0'), sale_item.dispatched_quantity - ret_qty)
                            sale_item.save(update_fields=['dispatched_quantity'])

                if self.debt_reduction_amount > Decimal('0') and sale.customer:
                    from apps.customers.models import CustomerTransaction
                    balance_before = sale.customer.current_balance
                    sale.customer.current_balance -= self.debt_reduction_amount
                    sale.customer.save(update_fields=['current_balance'])
                    CustomerTransaction.objects.create(
                        tenant=tenant,
                        customer=sale.customer,
                        transaction_type='CREDIT',
                        amount=self.debt_reduction_amount,
                        reference_id=sale.sale_number,
                        balance_before=balance_before,
                        balance_after=sale.customer.current_balance,
                        description=f"Debt cancellation from partial return ({self.refund_number})",
                        performed_by=disburser
                    )

                if self.refund_amount > Decimal('0.00'):
                    sale.amount_paid = max(Decimal('0.00'), sale.amount_paid - self.refund_amount)
                    p_note = f"[Partial return of {tenant.currency_symbol}{self.refund_amount} disbursed via {self.refund_number}]"
                    sale.notes = f"{sale.notes}\n{p_note}".strip() if sale.notes else p_note
                    sale.save()

                orig_pay_method = sale.payment_method
                if method == 'MOMO' and orig_pay_method != 'MOMO' and self.refund_amount > Decimal('0.00'):
                    from apps.accounting.models import DigitalFundWithdrawal
                    DigitalFundWithdrawal.objects.create(
                        tenant=tenant,
                        shop=sale.shop,
                        accountant=disburser,
                        amount=abs(self.refund_amount),
                        fund_source='MOMO',
                        notes=f"Partial return payout by cashier (Cross-payment): Sale {sale.sale_number} [{self.refund_number}]"
                    )
                elif method == 'CASH' and orig_pay_method == 'MOMO' and self.refund_amount > Decimal('0.00'):
                    from apps.accounting.models import DigitalFundWithdrawal
                    DigitalFundWithdrawal.objects.create(
                        tenant=tenant,
                        shop=sale.shop,
                        accountant=disburser,
                        amount=-abs(self.refund_amount),
                        fund_source='MOMO',
                        notes=f"Refund reversal (Cross-payment): Sale {sale.sale_number} [{self.refund_number}]"
                    )
                elif method == 'ECASH' and self.refund_amount > Decimal('0.00'):
                    from apps.payments.models import ECashLedger
                    if not ECashLedger.objects.filter(reference_type='RefundRequest', reference_id=self.pk).exists():
                        ECashLedger.objects.create(
                            tenant=tenant,
                            transaction_type='REFUND',
                            amount=-abs(self.refund_amount),
                            reference_type='RefundRequest',
                            reference_id=self.pk,
                            shop=sale.shop,
                            created_by=disburser,
                            notes=f"Partial return disbursed by cashier: Sale {sale.sale_number} [{self.refund_number}]"
                        )
            elif self.is_partial_halt:
                # Settle the sale to actual dispatched items and reduce amount_paid
                if sale.status in ['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']:
                    sale.settle_halted_sale(user=disburser, refund_req=self, refund_method=method)
                if self.refund_amount > Decimal('0.00') and sale.amount_paid > sale.total:
                    sale.amount_paid = max(Decimal('0.00'), sale.amount_paid - self.refund_amount)
                    p_note = f"[Partial refund of {tenant.currency_symbol}{self.refund_amount} disbursed via {self.refund_number}]"
                    sale.notes = f"{sale.notes}\n{p_note}".strip() if sale.notes else p_note
                    sale.save()

                orig_pay_method = sale.payment_method
                if method == 'MOMO' and orig_pay_method != 'MOMO' and self.refund_amount > Decimal('0.00'):
                    from apps.accounting.models import DigitalFundWithdrawal
                    DigitalFundWithdrawal.objects.create(
                        tenant=tenant,
                        shop=sale.shop,
                        accountant=disburser,
                        amount=abs(self.refund_amount),
                        fund_source='MOMO',
                        notes=f"Partial refund payout by cashier (Cross-payment): Sale {sale.sale_number} [{self.refund_number}]"
                    )
                elif method == 'CASH' and orig_pay_method == 'MOMO' and self.refund_amount > Decimal('0.00'):
                    from apps.accounting.models import DigitalFundWithdrawal
                    DigitalFundWithdrawal.objects.create(
                        tenant=tenant,
                        shop=sale.shop,
                        accountant=disburser,
                        amount=-abs(self.refund_amount),
                        fund_source='MOMO',
                        notes=f"Refund reversal (Cross-payment): Sale {sale.sale_number} [{self.refund_number}]"
                    )
                elif method == 'ECASH' and self.refund_amount > Decimal('0.00'):
                    from apps.payments.models import ECashLedger
                    if not ECashLedger.objects.filter(reference_type='RefundRequest', reference_id=self.pk).exists():
                        ECashLedger.objects.create(
                            tenant=tenant,
                            transaction_type='REFUND',
                            amount=-abs(self.refund_amount),
                            reference_type='RefundRequest',
                            reference_id=self.pk,
                            shop=sale.shop,
                            created_by=disburser,
                            notes=f"Partial refund disbursed by cashier: Sale {sale.sale_number} [{self.refund_number}]"
                        )
            else:
                # Full sale refund: return inventory & customer debt reversal (idempotent if already refunded)
                if sale.status != 'REFUNDED':
                    sale.refund(reason=self.reason, user=disburser)
                # Financial bucket deductions (CashTransfer / DigitalFundWithdrawal / ECashLedger)
                self._apply_financial_adjustment(disburser)

            # 2. Record disbursement metadata
            sale.is_halted = False
            sale.halt_snapshot = None
            sale.save(update_fields=['is_halted', 'halt_snapshot'])

            combined_notes = []
            if transaction_reference:
                combined_notes.append(f"Ref: {transaction_reference}")
            if payout_notes:
                combined_notes.append(payout_notes)
            notes_str = " | ".join(combined_notes)

            self.payout_status = 'PAID'
            self.paid_by = disburser
            self.paid_at = tz.now()
            if shift:
                self.shift = shift
            if notes_str:
                self.payout_notes = notes_str
            self.save()

        # 4. Notify requester and roles via in-app & bulletin
        from apps.notifications.services import notify_and_bulletin
        refund_type_str = "Partial Return" if self.is_partial_return else ("Partial Refund & Settle" if self.is_partial_halt else "Refund")
        amt_display = f"{tenant.currency_symbol}{self.refund_amount}" if (self.is_partial_halt or self.is_partial_return) else f"{tenant.currency_symbol}{sale.total}"
        notify_and_bulletin(
            tenant=tenant,
            title=f"{refund_type_str} Disbursed: {self.refund_number}",
            message=(
                f"Refund {self.refund_number} for Sale {sale.sale_number} ({amt_display}) "
                f"at {sale.shop.name} has been paid out by {disburser.get_full_name() or disburser.email} "
                f"via {self.get_refund_method_display()}."
            ),
            notification_type='REFUND_REQUEST',
            target_roles=['ACCOUNTANT', 'ADMIN', 'SHOP_MANAGER'],
            target_users=[self.requested_by] if self.requested_by else [],
            reference_type='RefundRequest',
            reference_id=self.pk,
            created_by=disburser,
            exclude_users=[disburser]
        )

    def _apply_financial_adjustment(self, approver):
        """
        Deduct the refunded amount from the appropriate financial bucket.
        Called inside an atomic block by approve().
        """
        from apps.accounting.models import CashTransfer, DigitalFundWithdrawal
        sale = self.sale
        tenant = self.tenant
        shop = sale.shop
        shop_manager = self.requested_by

        # The actual cash/funds the customer paid us
        paid_amount = sale.amount_paid
        
        if paid_amount <= 0:
            return

        # Find the accountant user for the to_user field on CashTransfer
        accountant_user = (
            User.objects.filter(
                tenant=tenant,
                role__name='ACCOUNTANT',
                is_active=True
            ).first()
            or User.objects.filter(
                tenant=tenant,
                role__name='ADMIN',
                is_active=True
            ).first()
            or approver
        )

        pay_method = sale.payment_method
        always_cash = tenant.refund_always_cash
        refund_method = self.refund_method

        # Since sale.status becomes REFUNDED, the sale's amount_paid is automatically
        # excluded from the shop's cash_sales, ecash_sales, or momo_sales aggregations.
        # This naturally drops the balance of whatever the original payment method was.

        if refund_method == 'ECASH':
            from apps.payments.models import ECashLedger
            if not ECashLedger.objects.filter(reference_type='RefundRequest', reference_id=self.pk).exists():
                ECashLedger.objects.create(
                    tenant=tenant,
                    transaction_type='REFUND',
                    amount=-abs(paid_amount),
                    reference_type='RefundRequest',
                    reference_id=self.pk,
                    shop=shop,
                    created_by=approver,
                    notes=f"Refund: Sale {sale.sale_number} [{self.refund_number}]"
                )
        elif refund_method != pay_method and refund_method == 'CASH' and pay_method in ('ECASH', 'MOMO'):
            # The sale was digital, but we are paying back in physical cash.
            # 1. Restore the digital balance (because excluding the sale artificially dropped it)
            DigitalFundWithdrawal.objects.create(
                tenant=tenant,
                shop=shop,
                accountant=approver,
                amount=-abs(paid_amount),   # Negative = reversal / money back out
                fund_source=pay_method,
                notes=(
                    f"Refund reversal (Cross-payment): Sale {sale.sale_number} "
                    f"[{self.refund_number}]"
                ),
            )
            
            # 2. Deduct physical cash via EXPENDITURE
            if shop_manager:
                CashTransfer.objects.create(
                    tenant=tenant,
                    amount=paid_amount,
                    transfer_type='EXPENDITURE',
                    status='CONFIRMED',
                    from_user=shop_manager,
                    from_location=shop,
                    to_user=accountant_user,
                    to_location=shop,
                    notes=(
                        f"Refund payout (Cross-payment): Sale {sale.sale_number} "
                        f"[{self.refund_number}] — {self.reason[:100]}"
                    ),
                    confirmed_at=timezone.now(),
                    confirmed_by=approver,
                )
        elif refund_method != pay_method and refund_method == 'MOMO' and pay_method in ('CASH', 'MIXED'):
            # Customer paid in cash, but refund is paid out from MOMO wallet.
            DigitalFundWithdrawal.objects.create(
                tenant=tenant,
                shop=shop,
                accountant=approver,
                amount=abs(paid_amount),
                fund_source='MOMO',
                notes=(
                    f"Refund payout (Cross-payment): Sale {sale.sale_number} "
                    f"[{self.refund_number}]"
                ),
            )

    def reject(self, approver, rejection_reason=''):
        """Reject the refund request and notify the shop manager."""
        from django.utils import timezone as tz

        if self.status != 'PENDING':
            raise ValidationError("Only pending refund requests can be rejected.")

        self.status = 'REJECTED'
        self.reviewed_by = approver
        self.reviewed_at = tz.now()
        self.rejection_reason = rejection_reason
        self.save()

        # Notify shop manager and roles via in-app & bulletin
        from apps.notifications.services import notify_and_bulletin
        notify_and_bulletin(
            tenant=self.tenant,
            title=f"Refund Request Rejected: {self.refund_number}",
            message=(
                f"Refund request {self.refund_number} for Sale {self.sale.sale_number} "
                f"({self.tenant.currency_symbol}{self.refund_amount or self.sale.total}) "
                f"at {self.sale.shop.name} was rejected by {approver.get_full_name() or approver.email}. "
                f"Reason: {rejection_reason or 'No reason given'}"
            ),
            notification_type='REFUND_REQUEST',
            target_roles=['SHOP_MANAGER', 'SHOP_CASHIER', 'ADMIN'],
            target_users=[self.requested_by] if self.requested_by else [],
            reference_type='RefundRequest',
            reference_id=self.pk,
            created_by=approver,
            exclude_users=[approver]
        )

    def cancel_approval(self, canceller, reason=''):
        """
        Cancel an approved refund request that has not yet been paid out / disbursed.
        Only allowed for Accountants and Admins while status == 'APPROVED' and payout_status == 'PENDING'.
        Reverts the request status to CANCELLED and unblocks the sale.
        If the sale was prematurely marked REFUNDED, reverts it back to COMPLETED / PENDING_DISPATCH
        and reverses any returned inventory or customer credit debt adjustments.
        """
        from django.db import transaction as db_transaction
        from django.utils import timezone as tz

        if self.status != 'APPROVED':
            raise ValidationError("Only approved refund requests can have their approval cancelled.")
        if self.payout_status == 'PAID':
            raise ValidationError("Cannot cancel a refund request that has already been paid out.")

        with db_transaction.atomic():
            self.status = 'CANCELLED'
            self.reviewed_by = canceller
            self.reviewed_at = tz.now()
            self.rejection_reason = reason or "Approval cancelled"
            self.save()

            sale = self.sale
            if sale.status == 'REFUNDED':
                if sale.tenant.use_strict_sales_workflow:
                    from django.db.models import F
                    has_undispatched = sale.items.filter(dispatched_quantity__lt=F('quantity')).exists()
                    has_dispatched = sale.items.filter(dispatched_quantity__gt=0).exists()
                    if has_undispatched and has_dispatched:
                        sale.status = 'PARTIALLY_DISPATCHED'
                    elif has_undispatched:
                        sale.status = 'PENDING_DISPATCH'
                    else:
                        sale.status = 'COMPLETED'
                else:
                    sale.status = 'COMPLETED'

                if sale.notes and sale.notes.startswith("REFUNDED"):
                    sale.notes = ""

                # Reverse any SALE_RETURN inventory ledger entries created by sale.refund()
                from apps.inventory.models import InventoryLedger
                return_ledgers = InventoryLedger.objects.filter(
                    reference_type='Refund',
                    reference_id=sale.pk,
                    transaction_type='SALE_RETURN'
                )
                if return_ledgers.exists():
                    for ret in return_ledgers:
                        InventoryLedger.objects.create(
                            tenant=sale.tenant,
                            product=ret.product,
                            batch=ret.batch,
                            location=ret.location,
                            transaction_type='SALE',
                            quantity=-ret.quantity,
                            unit_cost=ret.unit_cost,
                            reference_type='RefundCancellation',
                            reference_id=self.pk,
                            notes=f"Reversal of uncompleted refund return: {self.refund_number}",
                            created_by=canceller
                        )
                    # Restore dispatch tracking on items
                    for item in sale.items.all():
                        if item.dispatched_quantity == 0 and item.quantity > 0:
                            item.dispatched_quantity = item.quantity
                            item.save(update_fields=['dispatched_quantity'])
                    sale.is_dispatched = True

                # Reverse customer credit transaction if one was recorded
                if sale.customer:
                    from apps.customers.models import CustomerTransaction
                    refund_credit_tx = CustomerTransaction.objects.filter(
                        customer=sale.customer,
                        reference_id=sale.sale_number,
                        transaction_type='CREDIT',
                        description__startswith="Refund:"
                    ).order_by('-created_at').first()
                    if refund_credit_tx:
                        balance_before = sale.customer.current_balance
                        sale.customer.current_balance += refund_credit_tx.amount
                        sale.customer.save()
                        CustomerTransaction.objects.create(
                            tenant=sale.tenant,
                            customer=sale.customer,
                            transaction_type='DEBIT',
                            amount=refund_credit_tx.amount,
                            reference_id=sale.sale_number,
                            balance_before=balance_before,
                            balance_after=sale.customer.current_balance,
                            description=f"Refund Approval Cancelled: {self.refund_number}",
                            performed_by=canceller
                        )

                sale.save()

        # Notify shop manager / requester and roles via in-app & bulletin
        from apps.notifications.services import notify_and_bulletin
        refund_type_str = "Partial Refund & Settle" if self.is_partial_halt else "Refund"
        amt_display = f"{self.tenant.currency_symbol}{self.refund_amount}" if self.is_partial_halt else f"{self.tenant.currency_symbol}{self.sale.total}"
        notify_and_bulletin(
            tenant=self.tenant,
            title=f"{refund_type_str} Approval Cancelled: {self.refund_number}",
            message=(
                f"The approved refund request {self.refund_number} for Sale "
                f"{self.sale.sale_number} ({amt_display}) at {self.sale.shop.name} "
                f"has been cancelled by {canceller.get_full_name() or canceller.email}. "
                f"Reason: {reason or 'No reason given'}"
            ),
            notification_type='REFUND_REQUEST',
            target_roles=['SHOP_MANAGER', 'SHOP_CASHIER', 'ACCOUNTANT', 'ADMIN'],
            target_users=[self.requested_by] if self.requested_by else [],
            reference_type='RefundRequest',
            reference_id=self.pk,
            created_by=canceller,
            exclude_users=[canceller]
        )


