"""
Subscription models for the POS system.
Handles subscription plans, pricing, payments, and tenant manager assignments.
"""
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal
import uuid

from apps.core.models import Tenant, TenantModel, User


class SubscriptionPlan(models.Model):
    """
    Defines subscription plan tiers with pricing.
    Plans: Starter, Standard, Premium
    """
    PLAN_CODES = [
        ('STARTER', 'Starter'),
        ('STANDARD', 'Standard'),
        ('PREMIUM', 'Premium'),
    ]
    
    name = models.CharField(max_length=50)
    code = models.CharField(max_length=20, choices=PLAN_CODES, unique=True)
    description = models.TextField(blank=True)
    
    # Pricing
    base_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Monthly base price"
    )
    annual_base_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Monthly price when paying annually (discounted)"
    )
    max_shops = models.PositiveIntegerField(
        default=2,
        help_text="Maximum number of shop locations included"
    )
    additional_shop_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Price per additional shop beyond max_shops (for Premium)"
    )
    annual_additional_shop_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Additional shop price when paying annually (discounted)"
    )
    
    # Features (can be extended)
    features = models.JSONField(
        default=list,
        blank=True,
        help_text="List of feature descriptions"
    )
    
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['display_order', 'base_price']
        verbose_name = "Subscription Plan"
        verbose_name_plural = "Subscription Plans"
    
    def __str__(self):
        return f"{self.name} - {self.base_price}/month"
    
    def calculate_price(self, shop_count=0, annual=False):
        """
        Calculate total monthly price based on shop count.
        For Premium: base_price + (additional_shops * additional_shop_price)
        If annual=True, uses discounted annual pricing.
        """
        if annual:
            base = self.annual_base_price if self.annual_base_price else self.base_price
            shop_extra = self.annual_additional_shop_price if self.annual_additional_shop_price else self.additional_shop_price
        else:
            base = self.base_price
            shop_extra = self.additional_shop_price
        
        if self.code == 'PREMIUM' and shop_count > self.max_shops:
            additional_shops = shop_count - self.max_shops
            return base + (additional_shops * shop_extra)
        return base
    
    def get_annual_savings_percent(self):
        """Calculate percentage savings when paying annually."""
        if self.annual_base_price and self.base_price > 0:
            savings = ((self.base_price - self.annual_base_price) / self.base_price) * 100
            return round(savings, 0)
        return 0


class TenantPricingOverride(models.Model):
    """
    Custom pricing for individual tenants.
    Tenant Managers can adjust prices for specific tenants.
    """
    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name='pricing_override'
    )
    
    onboarding_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Custom onboarding fee (leave blank for default)"
    )
    monthly_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Custom monthly subscription price (leave blank for plan default)"
    )
    additional_shop_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Custom additional shop price (leave blank for plan default)"
    )
    
    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        help_text="Discount percentage applied to subscription"
    )
    
    notes = models.TextField(
        blank=True,
        help_text="Internal notes about this pricing arrangement"
    )
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='pricing_overrides_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Tenant Pricing Override"
        verbose_name_plural = "Tenant Pricing Overrides"
    
    def __str__(self):
        return f"Pricing Override for {self.tenant.name}"
    
    def get_effective_monthly_price(self, plan, shop_count=0):
        """Get the effective monthly price considering overrides."""
        if self.monthly_price is not None:
            base = self.monthly_price
        else:
            base = plan.base_price
        
        # Add additional shop costs for Premium
        if plan.code == 'PREMIUM' and shop_count > plan.max_shops:
            additional_shops = shop_count - plan.max_shops
            shop_price = self.additional_shop_price if self.additional_shop_price is not None else plan.additional_shop_price
            base += additional_shops * shop_price
        
        # Apply discount
        if self.discount_percentage > 0:
            discount = base * (self.discount_percentage / 100)
            base -= discount
        
        return base
    
    def get_effective_onboarding_fee(self, default_fee=Decimal('4500.00')):
        """Get the effective onboarding fee considering overrides."""
        if self.onboarding_fee is not None:
            return self.onboarding_fee
        return default_fee


class SubscriptionPayment(models.Model):
    """
    Payment history for subscriptions.
    Used for receipt generation.
    """
    PAYMENT_TYPE_CHOICES = [
        ('ONBOARDING', 'Onboarding Fee'),
        ('SUBSCRIPTION', 'Subscription'),
        ('RENEWAL', 'Renewal'),
        ('ADDITIONAL', 'Additional Payment'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    ]
    
    PAYMENT_METHOD_CHOICES = [
        ('CASH', 'Cash'),
        ('BANK_TRANSFER', 'Bank Transfer'),
        ('MOMO', 'Mobile Money'),
        ('PAYSTACK', 'Paystack'),
        ('MANUAL', 'Manual Entry'),
    ]
    
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='subscription_payments'
    )
    
    receipt_number = models.CharField(
        max_length=50,
        unique=True,
        editable=False,
        help_text="Unique receipt number"
    )
    
    payment_type = models.CharField(
        max_length=20,
        choices=PAYMENT_TYPE_CHOICES
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING'
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='MANUAL'
    )
    
    # Amount details
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    currency = models.CharField(max_length=3, default='GHS')
    
    # Subscription period (for subscription payments)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    
    # Payment references
    paystack_reference = models.CharField(max_length=100, blank=True)
    transaction_reference = models.CharField(max_length=100, blank=True)
    
    # Plan at time of payment (for historical reference)
    plan_name = models.CharField(max_length=50, blank=True)
    plan_details = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of plan details at payment time"
    )
    
    notes = models.TextField(blank=True)
    
    # Audit
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='subscription_payments_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Subscription Payment"
        verbose_name_plural = "Subscription Payments"
    
    def __str__(self):
        return f"{self.receipt_number} - {self.tenant.name} - {self.amount}"
    
    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = self.generate_receipt_number()
        super().save(*args, **kwargs)
    
    def generate_receipt_number(self):
        """Generate unique receipt number: SUB-YYYYMMDD-XXXX"""
        date_str = timezone.now().strftime('%Y%m%d')
        random_suffix = uuid.uuid4().hex[:6].upper()
        return f"SUB-{date_str}-{random_suffix}"
    
    def mark_completed(self):
        """Mark payment as completed."""
        self.status = 'COMPLETED'
        self.save(update_fields=['status', 'updated_at'])


class TenantManagerAssignment(models.Model):
    """
    Links Tenant Managers to the tenants they manage.
    """
    manager = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='managed_tenants',
        limit_choices_to={'role__name': 'TENANT_MANAGER'}
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='tenant_managers'
    )
    
    is_primary = models.BooleanField(
        default=False,
        help_text="Primary manager receives all notifications"
    )
    
    assigned_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='tenant_assignments_made'
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['manager', 'tenant']
        verbose_name = "Tenant Manager Assignment"
        verbose_name_plural = "Tenant Manager Assignments"
    
    def __str__(self):
        return f"{self.manager.get_full_name()} manages {self.tenant.name}"


class SubscriptionNotificationLog(models.Model):
    """
    Log of subscription-related notifications sent to tenants.
    Tracks daily notification requirements.
    """
    NOTIFICATION_TYPE_CHOICES = [
        ('EXPIRY_WARNING', 'Expiry Warning'),
        ('EXPIRED', 'Subscription Expired'),
        ('DEACTIVATION_WARNING', 'Deactivation Warning'),
        ('DEACTIVATED', 'Account Deactivated'),
        ('LOCKED', 'Account Locked'),
    ]
    
    CHANNEL_CHOICES = [
        ('EMAIL', 'Email'),
        ('SMS', 'SMS'),
        ('IN_APP', 'In-App'),
    ]
    
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='subscription_notifications'
    )
    
    notification_type = models.CharField(
        max_length=30,
        choices=NOTIFICATION_TYPE_CHOICES
    )
    channel = models.CharField(
        max_length=20,
        choices=CHANNEL_CHOICES
    )
    
    recipient_email = models.EmailField(blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True)
    
    message_preview = models.TextField(
        blank=True,
        help_text="Preview/summary of the message sent"
    )
    
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = "Subscription Notification Log"
        verbose_name_plural = "Subscription Notification Logs"
    
    def __str__(self):
        return f"{self.notification_type} to {self.tenant.name} via {self.channel}"
    
    def mark_sent(self):
        """Mark notification as successfully sent."""
        self.is_sent = True
        self.sent_at = timezone.now()
        self.save(update_fields=['is_sent', 'sent_at'])
    
    def mark_failed(self, error_message):
        """Mark notification as failed with error."""
        self.is_sent = False
        self.error_message = error_message
        self.save(update_fields=['is_sent', 'error_message'])
