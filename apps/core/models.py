"""
Core models for multi-tenant POS system.
Includes: Tenant, Location, Role, and custom User model.
"""
from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.text import slugify
from django.utils import timezone


class Tenant(models.Model):
    """
    Represents an organization/company in the multi-tenant system.
    Each tenant has their own isolated data (locations, products, users, etc.)
    """
    CURRENCY_CHOICES = [
        ('GHS', 'Ghana Cedis (GH₵)'),
        ('NGN', 'Nigerian Naira (₦)'),
        ('XOF', 'CFA Franc BCEAO (CFA)'),
        ('XAF', 'CFA Franc BEAC (CFA)'),
        ('USD', 'US Dollar ($)'),
        ('GBP', 'British Pound (£)'),
        ('EUR', 'Euro (€)'),
    ]
    
    CURRENCY_SYMBOLS = {
        'GHS': 'GH₵',
        'NGN': '₦',
        'XOF': 'CFA',
        'XAF': 'CFA',
        'USD': '$',
        'GBP': '£',
        'EUR': '€',
    }
    
    name = models.CharField(max_length=255, verbose_name="Organization Name")
    slug = models.SlugField(max_length=255, unique=True)
    email = models.EmailField(verbose_name="Organization Email")
    phone = models.CharField(max_length=20, verbose_name="Organization Phone")
    address = models.TextField(blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='GHS')
    logo = models.ImageField(upload_to='tenant_logos/', blank=True, null=True)
    
    # Settings (can be expanded via JSON in the future)
    allow_negative_stock = models.BooleanField(default=False)
    require_refund_approval = models.BooleanField(default=True)
    require_return_approval = models.BooleanField(default=True)
    credit_limit_warning_percent = models.IntegerField(default=80)
    backdating_allowed_days = models.IntegerField(default=0)
    
    # Shop Manager permissions
    shop_manager_can_add_products = models.BooleanField(
        default=False,
        help_text="Allow shop managers to add new products"
    )
    shop_manager_can_receive_stock = models.BooleanField(
        default=False,
        help_text="Allow shop managers to receive stock (create batches)"
    )
    
    # Cash Transfer settings
    allow_accountant_to_shop_transfers = models.BooleanField(
        default=False,
        help_text="Allow accountants to send cash (float/change) to shops"
    )
    
    # Subscription Management
    SUBSCRIPTION_STATUS_CHOICES = [
        ('TRIAL', 'Trial'),
        ('ACTIVE', 'Active'),
        ('EXPIRED', 'Expired'),
        ('SUSPENDED', 'Suspended'),
        ('INACTIVE', 'Inactive'),  # Can login but cannot transact
        ('LOCKED', 'Locked'),  # Cannot access account (6-month inactive)
    ]
    
    subscription_plan = models.ForeignKey(
        'subscriptions.SubscriptionPlan',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tenants',
        help_text="Current subscription plan"
    )
    subscription_status = models.CharField(
        max_length=20,
        choices=SUBSCRIPTION_STATUS_CHOICES,
        default='TRIAL',
        help_text="Current subscription status"
    )
    subscription_start_date = models.DateField(
        null=True,
        blank=True,
        help_text="When subscription started"
    )
    subscription_end_date = models.DateField(
        null=True,
        blank=True,
        help_text="When subscription expires"
    )
    auto_renew = models.BooleanField(
        default=False,
        help_text="If True, subscription won't auto-expire"
    )
    
    # Setup tracking
    setup_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When tenant completed initial setup"
    )
    onboarding_paid = models.BooleanField(
        default=False,
        help_text="Whether onboarding fee has been paid"
    )
    
    # Notification tracking
    last_notification_sent = models.DateField(
        null=True,
        blank=True,
        help_text="Date of last subscription notification"
    )
    
    # Account locking (6-month inactive)
    locked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When account was locked due to extended inactivity"
    )
    
    admin_notes = models.TextField(
        blank=True,
        help_text="Internal notes for superuser (not visible to tenant)"
    )
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            # Ensure uniqueness
            original_slug = self.slug
            counter = 1
            while Tenant.objects.filter(slug=self.slug).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        
        # Set default subscription dates if not set
        if not self.subscription_start_date:
            self.subscription_start_date = timezone.now().date()
        if not self.subscription_end_date:
            # Default to 1 year from start
            from datetime import timedelta
            self.subscription_end_date = self.subscription_start_date + timedelta(days=365)
        
        super().save(*args, **kwargs)
    
    @property
    def currency_symbol(self):
        return self.CURRENCY_SYMBOLS.get(self.currency, self.currency)
    
    @property
    def is_subscription_valid(self):
        """Check if subscription is currently valid."""
        if self.subscription_status in ['EXPIRED', 'SUSPENDED', 'INACTIVE', 'LOCKED']:
            return False
        if self.subscription_end_date and self.subscription_end_date < timezone.now().date():
            return False
        return True
    
    @property
    def can_transact(self):
        """Check if tenant can perform transactions (not just login)."""
        if self.subscription_status in ['INACTIVE', 'LOCKED', 'EXPIRED', 'SUSPENDED']:
            return False
        if not self.is_active:
            return False
        return True
    
    @property
    def is_locked(self):
        """Check if account is locked due to extended inactivity."""
        return self.subscription_status == 'LOCKED' or self.locked_at is not None
    
    @property
    def days_until_expiry(self):
        """Get days until subscription expires."""
        if self.subscription_end_date:
            delta = self.subscription_end_date - timezone.now().date()
            return delta.days
        return None
    
    @property
    def days_since_expiry(self):
        """Get days since subscription expired (negative means not expired yet)."""
        if self.days_until_expiry is not None:
            return -self.days_until_expiry
        return None
    
    @property
    def is_in_setup_period(self):
        """Check if tenant is still in the 14-day setup period."""
        if not self.setup_completed_at:
            return True  # Setup not completed yet
        from datetime import timedelta
        setup_end = self.setup_completed_at + timedelta(days=14)
        return timezone.now() < setup_end
    
    @property
    def subscription_status_display(self):
        """Return a display-friendly status with warning for expiring soon."""
        if self.subscription_status in ['EXPIRED', 'SUSPENDED', 'INACTIVE', 'LOCKED']:
            return self.get_subscription_status_display()
        if self.days_until_expiry is not None and self.days_until_expiry <= 30:
            return f"{self.get_subscription_status_display()} (Expires in {self.days_until_expiry} days)"
        return self.get_subscription_status_display()
    
    def get_shop_count(self):
        """Get the number of shop locations for this tenant."""
        return self.location_set.filter(location_type='SHOP', is_active=True).count()
    
    def get_monthly_subscription_price(self):
        """Calculate monthly subscription price considering plan and shop count."""
        if not self.subscription_plan:
            return None
        
        shop_count = self.get_shop_count()
        
        # Check for pricing override
        try:
            override = self.pricing_override
            return override.get_effective_monthly_price(self.subscription_plan, shop_count)
        except Exception:
            pass
        
        return self.subscription_plan.calculate_price(shop_count)


class TenantModel(models.Model):
    """
    Abstract base model for all tenant-scoped models.
    Automatically filters queryset by tenant.
    """
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="%(class)s_set"
    )
    
    class Meta:
        abstract = True


class Location(TenantModel):
    """
    Represents a physical location: Production, Stores (warehouse), or Shop.
    """
    LOCATION_TYPES = [
        ('PRODUCTION', 'Production'),
        ('STORES', 'Stores/Warehouse'),
        ('SHOP', 'Shop'),
    ]
    
    name = models.CharField(max_length=255)
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPES)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    
    # Shop-specific settings
    receipt_copies = models.PositiveIntegerField(
        default=1,
        help_text="Number of receipt copies to print per sale (for shops)"
    )
    
    # Status fields
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        unique_together = ['tenant', 'name']
    
    def __str__(self):
        return f"{self.name} ({self.get_location_type_display()})"


class Role(models.Model):
    """
    System-wide roles with predefined permissions.
    """
    ROLE_CHOICES = [
        ('SUPER_ADMIN', 'Super Admin'),
        ('TENANT_MANAGER', 'Tenant Manager'),  # Manages subscriptions for clients
        ('ADMIN', 'Admin'),
        ('PRODUCTION_MANAGER', 'Production Manager'),
        ('STORES_MANAGER', 'Stores Manager'),
        ('SHOP_MANAGER', 'Shop Manager'),
        ('SHOP_ATTENDANT', 'Shop Attendant'),
        ('ACCOUNTANT', 'Accountant'),
        ('AUDITOR', 'Auditor'),
    ]
    
    name = models.CharField(max_length=50, choices=ROLE_CHOICES, unique=True)
    description = models.TextField(blank=True)
    
    # Permissions (can be expanded)
    can_manage_users = models.BooleanField(default=False)
    can_manage_inventory = models.BooleanField(default=False)
    can_manage_sales = models.BooleanField(default=False)
    can_view_reports = models.BooleanField(default=False)
    can_approve_refunds = models.BooleanField(default=False)
    can_approve_returns = models.BooleanField(default=False)
    can_manage_accounting = models.BooleanField(default=False)
    can_view_audit_logs = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.get_name_display()


class UserManager(BaseUserManager):
    """Custom user manager for the User model."""
    
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """
    Custom user model for the POS system.
    Uses email as the unique identifier instead of username.
    """
    username = None  # Remove username field
    email = models.EmailField(unique=True, verbose_name="Email Address")
    
    # Tenant association (NULL until tenant setup is complete for admins)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='users'
    )
    
    # Role and location
    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='users'
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users'
    )
    
    # Profile fields
    phone = models.CharField(max_length=20, blank=True)
    profile_image = models.ImageField(upload_to='profile_images/', blank=True, null=True)
    
    # Tenant setup tracking
    is_tenant_setup_complete = models.BooleanField(
        default=True,
        help_text="Set to False for new Admin users who need to set up their tenant"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    
    # Password reset tracking
    password_reset_required = models.BooleanField(
        default=False,
        help_text="If True, user must change password on next login"
    )
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    
    objects = UserManager()
    
    class Meta:
        ordering = ['first_name', 'last_name']
    
    def __str__(self):
        return f"{self.get_full_name()} ({self.email})"
    
    @property
    def is_super_admin(self):
        return self.is_superuser or (self.role and self.role.name == 'SUPER_ADMIN')
    
    @property
    def is_admin(self):
        return self.role and self.role.name == 'ADMIN'
    
    @property
    def needs_tenant_setup(self):
        """Check if user needs to set up their tenant."""
        return self.is_admin and not self.tenant and not self.is_tenant_setup_complete


class ContactMessage(models.Model):
    """
    Store contact form submissions from the landing page.
    Displayed in the superadmin dashboard for follow-up.
    """
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50)
    email = models.EmailField(blank=True)
    message = models.TextField(blank=True)
    whatsapp_contact = models.BooleanField(default=False)
    
    # Tracking
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, help_text="Internal notes for follow-up")
    
    # Notification status
    telegram_sent = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} - {self.phone} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"
    
    def mark_as_read(self):
        """Mark message as read."""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])

