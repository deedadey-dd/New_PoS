"""
Payment provider models and e-cash tracking.
Handles tenant-level payment configuration and e-cash transactions.
"""
from django.db import models
from django.db.models import Sum
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.conf import settings
from decimal import Decimal
import base64
import hashlib

from apps.core.models import TenantModel, User, Location

# Simple encryption for API keys (in production, use django-fernet-fields or similar)
try:
    from cryptography.fernet import Fernet
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False


def get_encryption_key():
    """Get or generate encryption key from Django's SECRET_KEY."""
    # Derive a 32-byte key from SECRET_KEY
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_value(value):
    """Encrypt a string value."""
    if not value:
        return value
    if not ENCRYPTION_AVAILABLE:
        return value  # Fallback: store as-is (not recommended for production)
    
    fernet = Fernet(get_encryption_key())
    return fernet.encrypt(value.encode()).decode()


def decrypt_value(value):
    """Decrypt an encrypted string value."""
    if not value:
        return value
    if not ENCRYPTION_AVAILABLE:
        return value
    
    try:
        fernet = Fernet(get_encryption_key())
        return fernet.decrypt(value.encode()).decode()
    except Exception:
        # If decryption fails, return as-is (might be unencrypted legacy data)
        return value


class PaymentProviderSettings(TenantModel):
    """
    Tenant-level payment provider configuration.
    Supports multiple providers (currently Paystack, extensible for future).
    """
    PROVIDER_CHOICES = [
        ('PAYSTACK', 'Paystack'),
        ('FLUTTERWAVE', 'Flutterwave'),  # Future
        ('MTN_MOMO', 'MTN Mobile Money'),  # Future
        ('VODAFONE_CASH', 'Vodafone Cash'),  # Future
    ]
    
    provider = models.CharField(
        max_length=20,
        choices=PROVIDER_CHOICES,
        default='PAYSTACK'
    )
    is_active = models.BooleanField(
        default=False,
        help_text="Enable this payment provider"
    )
    
    # API Credentials (encrypted at rest)
    public_key = models.CharField(
        max_length=512,
        blank=True,
        help_text="Public/Test key for frontend"
    )
    _secret_key = models.CharField(
        max_length=512,
        blank=True,
        db_column='secret_key',
        help_text="Secret key (encrypted)"
    )
    _webhook_secret = models.CharField(
        max_length=512,
        blank=True,
        db_column='webhook_secret',
        help_text="Webhook secret for verifying callbacks (encrypted)"
    )
    
    # Callback configuration
    callback_url = models.URLField(
        blank=True,
        help_text="Auto-generated webhook URL for this tenant"
    )
    
    # Test mode
    test_mode = models.BooleanField(
        default=True,
        help_text="Use test/sandbox credentials"
    )
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Payment Provider Settings"
        verbose_name_plural = "Payment Provider Settings"
        unique_together = ['tenant', 'provider']
    
    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        mode = "Test" if self.test_mode else "Live"
        return f"{self.get_provider_display()} - {self.tenant.name} ({status}, {mode})"
    
    @property
    def secret_key(self):
        """Decrypt and return the secret key."""
        return decrypt_value(self._secret_key)
    
    @secret_key.setter
    def secret_key(self, value):
        """Encrypt and store the secret key."""
        self._secret_key = encrypt_value(value) if value else ''
    
    @property
    def webhook_secret(self):
        """Decrypt and return the webhook secret."""
        return decrypt_value(self._webhook_secret)
    
    @webhook_secret.setter
    def webhook_secret(self, value):
        """Encrypt and store the webhook secret."""
        self._webhook_secret = encrypt_value(value) if value else ''
    
    def get_masked_secret_key(self):
        """Return masked version for display."""
        key = self.secret_key
        if not key:
            return ''
        if len(key) <= 8:
            return '*' * len(key)
        return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


class ECashLedger(TenantModel):
    """
    Ledger tracking all e-cash transactions.
    Every e-cash payment and withdrawal creates an entry here.
    """
    TRANSACTION_TYPES = [
        ('PAYMENT', 'E-Cash Payment Received'),
        ('WITHDRAWAL', 'E-Cash Withdrawn to Cash'),
        ('REFUND', 'E-Cash Refund'),
        ('ADJUSTMENT', 'Manual Adjustment'),
    ]
    
    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Positive for incoming, negative for outgoing"
    )
    
    # Running balance (calculated on save)
    balance_after = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0')
    )
    
    # Reference to source transaction
    reference_type = models.CharField(
        max_length=50,
        blank=True,
        help_text="Type of source: Sale, Withdrawal, Refund"
    )
    reference_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="ID of the source record"
    )
    
    # Paystack specific
    paystack_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Paystack transaction reference"
    )
    provider = models.CharField(
        max_length=20,
        default='PAYSTACK',
        help_text="Payment provider used"
    )
    
    # Shop-level tracking (added for shop-specific e-cash withdrawals)
    shop = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ecash_transactions',
        help_text="Shop where this e-cash transaction occurred"
    )
    
    # Audit
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ecash_transactions'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "E-Cash Ledger Entry"
        verbose_name_plural = "E-Cash Ledger"
    
    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"
    
    def save(self, *args, **kwargs):
        # Calculate running balance
        if not self.pk:
            last_entry = ECashLedger.objects.filter(
                tenant=self.tenant
            ).order_by('-created_at', '-pk').first()
            
            previous_balance = last_entry.balance_after if last_entry else Decimal('0')
            self.balance_after = previous_balance + self.amount
        
        super().save(*args, **kwargs)
    
    @classmethod
    def get_current_balance(cls, tenant):
        """Get the current e-cash balance for a tenant."""
        last_entry = cls.objects.filter(tenant=tenant).order_by('-created_at', '-pk').first()
        return last_entry.balance_after if last_entry else Decimal('0')
    
    @classmethod
    def get_shop_balance(cls, tenant, shop):
        """Get the current e-cash balance for a specific shop."""
        result = cls.objects.filter(
            tenant=tenant,
            shop=shop
        ).aggregate(total=Sum('amount'))
        return result['total'] or Decimal('0')
    
    @classmethod
    def record_payment(cls, tenant, amount, sale=None, paystack_ref='', user=None, notes='', shop=None):
        """Record an e-cash payment from a sale or payment on account."""
        # Determine reference info based on sale presence
        if sale:
            reference_type = 'Sale'
            reference_id = sale.pk
            auto_notes = f"E-Cash payment for Sale {sale.sale_number}"
            # Use sale's shop if not explicitly provided
            if not shop and hasattr(sale, 'shop'):
                shop = sale.shop
        else:
            reference_type = 'Payment'
            reference_id = None
            auto_notes = "E-Cash payment on account"
        
        return cls.objects.create(
            tenant=tenant,
            transaction_type='PAYMENT',
            amount=amount,
            reference_type=reference_type,
            reference_id=reference_id,
            paystack_reference=paystack_ref,
            created_by=user,
            notes=notes if notes else auto_notes,
            shop=shop
        )
    
    @classmethod
    def record_withdrawal(cls, tenant, amount, withdrawal, user, shop=None):
        """Record an e-cash withdrawal to cash."""
        return cls.objects.create(
            tenant=tenant,
            transaction_type='WITHDRAWAL',
            amount=-abs(amount),  # Negative = outgoing
            reference_type='ECashWithdrawal',
            reference_id=withdrawal.pk,
            created_by=user,
            notes=f"Withdrawal from {shop.name if shop else 'all shops'} by {user.get_full_name() or user.email}",
            shop=shop
        )


class ECashWithdrawal(TenantModel):
    """
    Withdrawal of e-cash to physical cash by Accountant.
    When completed, reduces e-cash balance and increases accountant's cash-on-hand.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING'
    )
    
    # Who initiated the withdrawal
    withdrawn_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='ecash_withdrawals'
    )
    
    # Completion tracking
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    
    # Reference number (auto-generated)
    withdrawal_number = models.CharField(max_length=50, blank=True)
    
    # Shop this withdrawal is from (null = tenant-wide withdrawal)
    shop = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ecash_withdrawals',
        help_text="Shop to withdraw e-cash from (leave blank for tenant-wide)"
    )
    
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "E-Cash Withdrawal"
        verbose_name_plural = "E-Cash Withdrawals"
    
    def __str__(self):
        return f"Withdrawal {self.withdrawal_number or self.pk} - {self.amount}"
    
    def save(self, *args, **kwargs):
        # Auto-generate withdrawal number
        if not self.withdrawal_number:
            today = timezone.now().strftime('%Y%m%d')
            last = ECashWithdrawal.objects.filter(
                tenant=self.tenant,
                withdrawal_number__startswith=f"EW{today}"
            ).order_by('-withdrawal_number').first()
            
            if last and last.withdrawal_number:
                try:
                    num = int(last.withdrawal_number[-4:]) + 1
                except ValueError:
                    num = 1
            else:
                num = 1
            
            self.withdrawal_number = f"EW{today}{num:04d}"
        
        super().save(*args, **kwargs)
    
    def complete(self, user=None):
        """Complete the withdrawal - records in ECashLedger."""
        if self.status != 'PENDING':
            raise ValidationError("Only pending withdrawals can be completed.")
        
        # Check if sufficient e-cash balance (shop-specific or tenant-wide)
        if self.shop:
            current_balance = ECashLedger.get_shop_balance(self.tenant, self.shop)
        else:
            current_balance = ECashLedger.get_current_balance(self.tenant)
            
        if current_balance < self.amount:
            raise ValidationError(
                f"Insufficient e-cash balance. Available: {current_balance}, Requested: {self.amount}"
            )
        
        # Record in ledger
        ECashLedger.record_withdrawal(
            tenant=self.tenant,
            amount=self.amount,
            withdrawal=self,
            user=user or self.withdrawn_by,
            shop=self.shop
        )
        
        # Update status
        self.status = 'COMPLETED'
        self.completed_at = timezone.now()
        self.save()
        
        # Create notification
        from apps.notifications.models import Notification
        shop_name = self.shop.name if self.shop else 'all shops'
        Notification.objects.create(
            tenant=self.tenant,
            user=self.withdrawn_by,
            title="E-Cash Withdrawal Completed",
            message=f"E-cash withdrawal of {self.amount} from {shop_name} has been completed. "
                    f"Please add this to your physical cash count.",
            notification_type='SYSTEM',
            reference_type='ECashWithdrawal',
            reference_id=self.pk
        )
        
        return self
    
    def cancel(self, reason=''):
        """Cancel the withdrawal."""
        if self.status != 'PENDING':
            raise ValidationError("Only pending withdrawals can be cancelled.")
        
        self.status = 'CANCELLED'
        self.cancelled_at = timezone.now()
        self.cancellation_reason = reason
        self.save()
