from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
from apps.core.models import TenantModel, User

class Customer(TenantModel):
    """
    Represents a customer who can purchase items, potentially on credit.
    """
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    
    # Optional link to specific shop (if None, global customer?)
    # User requirement: "these customers should be linked to a shop"
    shop = models.ForeignKey(
        'core.Location',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='customers',
        limit_choices_to={'location_type': 'SHOP'}
    )
    
    # Financials
    current_balance = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="Positive value means customer owes money (Debt). Negative means they have credit."
    )
    credit_limit = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Maximum debt allowed. Leave empty for no limit."
    )
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.phone})"

class CustomerTransaction(TenantModel):
    """
    Ledger for customer financial transactions (Debts and Payments).
    """
    TRANSACTION_TYPES = [
        ('DEBIT', 'Debit (Purchase)'),   # Increases debt
        ('CREDIT', 'Credit (Payment)'),  # Decreases debt
        ('ADJUSTMENT', 'Adjustment'),    # Correction
    ]

    customer = models.ForeignKey(
        Customer, 
        on_delete=models.PROTECT, 
        related_name='transactions'
    )
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    description = models.CharField(max_length=255)
    
    # Links to other parts of the system
    reference_id = models.CharField(max_length=100, blank=True, help_text="ID of linked Sale or Payment")
    
    # Audit
    balance_before = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    
    performed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='customer_transactions'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount} for {self.customer.name}"
