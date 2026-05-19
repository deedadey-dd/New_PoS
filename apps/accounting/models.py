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
        ('DEPOSIT', 'Cash Deposit'),       # Shop → Accountant
        ('FLOAT', 'Float/Change'),          # Accountant → Shop
        ('EXPENDITURE', 'Expenditure'),     # Shop cash spent on approved expenditure
    ]
    
    # Transfer details
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    transfer_type = models.CharField(max_length=15, choices=TYPE_CHOICES)
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

class BankTransfer(TenantModel):
    """
    Records a bank deposit made by an accountant, removing funds from their dashboard.
    """
    FUND_CHOICES = [
        ('CASH', 'Physical Cash'),
        ('ECASH', 'E-Cash'),
        ('MOMO', 'Local Momo'),
    ]
    
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    fund_source = models.CharField(max_length=10, choices=FUND_CHOICES)
    teller_name = models.CharField(max_length=255)
    notes = models.TextField(blank=True, help_text="Additional information about the deposit")
    
    accountant = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='bank_transfers'
    )
    
    receipt_number = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Bank Transfer"
        verbose_name_plural = "Bank Transfers"
        
    def __str__(self):
        return f"{self.get_fund_source_display()} to Bank - {self.amount} ({self.created_at.strftime('%Y-%m-%d')})"
        
    def save(self, *args, **kwargs):
        # Auto-generate receipt number
        if not self.receipt_number:
            today = timezone.now().strftime('%Y%m%d')
            last = BankTransfer.objects.filter(
                tenant=self.tenant,
                receipt_number__startswith=f"BT{today}"
            ).order_by('-receipt_number').first()
            
            if last and last.receipt_number:
                try:
                    num = int(last.receipt_number[-4:]) + 1
                except ValueError:
                    num = 1
            else:
                num = 1
            
            self.receipt_number = f"BT{today}{num:04d}"
        
        super().save(*args, **kwargs)


class ExpenditureCategory(TenantModel):
    """
    User-defined categories for expenditures (e.g. Transportation, Utilities).
    Admins can add custom categories on top of the seeded defaults.
    """
    DEFAULT_CATEGORIES = ['Transportation', 'Utilities', 'Stationery', 'Others']

    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text="Seeded default category — name can be changed but it cannot be deleted"
    )

    class Meta:
        ordering = ['name']
        unique_together = ['tenant', 'name']

    def __str__(self):
        return self.name

    @classmethod
    def seed_defaults(cls, tenant):
        """Create the default categories for a tenant if they don't exist."""
        for cat_name in cls.DEFAULT_CATEGORIES:
            cls.objects.get_or_create(
                tenant=tenant,
                name=cat_name,
                defaults={'is_default': True}
            )


class ExpenditureRequest(TenantModel):
    """
    Groups multiple expenditure items into a single request/voucher.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending Approval'),
        ('PARTIAL', 'Partially Approved'),
        ('FULLY_APPROVED', 'Fully Approved'),
        ('REJECTED', 'Rejected'),
    ]

    voucher_number = models.CharField(max_length=20, unique=True, editable=False)
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name='expenditure_requests'
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='expenditure_requests_made'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    notes = models.TextField(blank=True, help_text="Overall notes for this voucher")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.voucher_number} - {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if not self.voucher_number:
            date_str = timezone.now().strftime('%Y%m%d')
            count = ExpenditureRequest.objects.filter(created_at__date=timezone.now().date()).count() + 1
            self.voucher_number = f"EXP-{date_str}-{count:04d}"
        super().save(*args, **kwargs)

    @property
    def total_amount(self):
        """Total amount of all items in this request."""
        return self.items.aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')

    @property
    def approved_amount(self):
        """Total amount of approved items only."""
        return self.items.filter(status='APPROVED').aggregate(total=models.Sum('amount'))['total'] or Decimal('0.00')

    def update_status(self):
        """Update the overall status based on item statuses."""
        items = self.items.all()
        if not items.exists():
            return

        total_count = items.count()
        approved_count = items.filter(status='APPROVED').count()
        rejected_count = items.filter(status='REJECTED').count()
        pending_count = items.filter(status='PENDING').count()

        if pending_count > 0:
            self.status = 'PENDING'
        elif approved_count == total_count:
            self.status = 'FULLY_APPROVED'
        elif rejected_count == total_count:
            self.status = 'REJECTED'
        elif approved_count > 0:
            self.status = 'PARTIAL'
        else:
            self.status = 'REJECTED'

        self.save()


class ExpenditureItem(TenantModel):
    """
    Individual items within an expenditure voucher.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    SOURCE_CHOICES = [
        ('ACCOUNTANT', 'From Accountant'),
        ('SHOP_CASH', "From Shop's Cash-on-Hand"),
    ]

    request = models.ForeignKey(
        ExpenditureRequest,
        on_delete=models.CASCADE,
        related_name='items'
    )
    category = models.ForeignKey(
        ExpenditureCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='items'
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    description = models.TextField(help_text="Detailed description of this item")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='PENDING')
    source_of_funds = models.CharField(max_length=20, choices=SOURCE_CHOICES, null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_expenditure_items'
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    def approve(self, user, source_of_funds):
        if self.status != 'PENDING':
            raise ValidationError("This item is already processed.")
        if not source_of_funds:
            raise ValidationError("Source of funds is required for approval.")
        if source_of_funds not in dict(self.SOURCE_CHOICES).keys():
            raise ValidationError("Invalid source of funds.")

        self.status = 'APPROVED'
        self.source_of_funds = source_of_funds
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save()
        self.request.update_status()

        # --- SHOP_CASH: deduct from shop cash-on-hand via auto-confirmed CashTransfer ---
        if source_of_funds == 'SHOP_CASH':
            manager = self.request.requested_by
            location = self.request.location
            if manager:
                ct = CashTransfer(
                    tenant=self.tenant,
                    amount=self.amount,
                    transfer_type='EXPENDITURE',  # Distinct type — shop cash spent on expense
                    status='CONFIRMED',
                    from_user=manager,    # deducted from shop manager's cash-on-hand
                    from_location=location,
                    to_user=user,         # the approving accountant — acts as the authoriser
                    to_location=location,
                    notes=(
                        f"Expenditure: {self.description} "
                        f"[Voucher {self.request.voucher_number}]"
                    ),
                    confirmed_at=timezone.now(),
                    confirmed_by=user,
                )
                ct.save()

        # --- Notify the shop manager who created this voucher ---
        manager = self.request.requested_by
        if manager:
            from apps.notifications.models import Notification
            Notification.objects.create(
                tenant=self.tenant,
                user=manager,
                title="Expenditure Item Approved",
                message=(
                    f"Your expenditure item \"{self.description}\" "
                    f"({self.tenant.currency} {self.amount}) on voucher "
                    f"{self.request.voucher_number} was approved by "
                    f"{user.get_full_name()}. "
                    f"Source: {self.get_source_of_funds_display()}."
                ),
                notification_type='SYSTEM',
                reference_type='ExpenditureRequest',
                reference_id=self.request.pk,
            )

    def reject(self, user, reason):
        if self.status != 'PENDING':
            raise ValidationError("This item is already processed.")
        self.status = 'REJECTED'
        self.approved_by = user
        self.approved_at = timezone.now()
        self.rejection_reason = reason
        self.save()
        self.request.update_status()

        # --- Notify the shop manager who created this voucher ---
        manager = self.request.requested_by
        if manager:
            from apps.notifications.models import Notification
            Notification.objects.create(
                tenant=self.tenant,
                user=manager,
                title="Expenditure Item Rejected",
                message=(
                    f"Your expenditure item \"{self.description}\" "
                    f"({self.tenant.currency} {self.amount}) on voucher "
                    f"{self.request.voucher_number} was rejected by "
                    f"{user.get_full_name()}. "
                    f"Reason: {reason or 'No reason given'}."
                ),
                notification_type='SYSTEM',
                reference_type='ExpenditureRequest',
                reference_id=self.request.pk,
            )

