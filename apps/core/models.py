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
        super().save(*args, **kwargs)
    
    @property
    def currency_symbol(self):
        return self.CURRENCY_SYMBOLS.get(self.currency, self.currency)


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
    
    # Shop-specific fields
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
