"""
Base payment provider interface.
All payment providers should inherit from this class.
"""
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, Optional, Tuple


class PaymentResult:
    """Result of a payment operation."""
    def __init__(
        self,
        success: bool,
        reference: str = '',
        message: str = '',
        data: Optional[Dict] = None,
        amount: Decimal = Decimal('0'),
        authorization_url: str = ''
    ):
        self.success = success
        self.reference = reference
        self.message = message
        self.data = data or {}
        self.amount = amount
        self.authorization_url = authorization_url
    
    def __bool__(self):
        return self.success
    
    def to_dict(self):
        return {
            'success': self.success,
            'reference': self.reference,
            'message': self.message,
            'data': self.data,
            'amount': str(self.amount),
            'authorization_url': self.authorization_url,
        }


class BasePaymentProvider(ABC):
    """
    Abstract base class for payment providers.
    Implement this for each payment provider (Paystack, Flutterwave, etc.)
    """
    
    def __init__(self, settings):
        """
        Initialize with PaymentProviderSettings instance.
        
        Args:
            settings: PaymentProviderSettings model instance
        """
        self.settings = settings
        self.public_key = settings.public_key
        self.secret_key = settings.secret_key
        self.test_mode = settings.test_mode
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass
    
    @abstractmethod
    def initialize_payment(
        self,
        amount: Decimal,
        email: str,
        reference: str,
        callback_url: str = '',
        metadata: Optional[Dict] = None
    ) -> PaymentResult:
        """
        Initialize a payment transaction.
        
        Args:
            amount: Amount in major currency units (e.g., GHS)
            email: Customer email
            reference: Unique transaction reference
            callback_url: URL to redirect after payment
            metadata: Additional data to attach to transaction
            
        Returns:
            PaymentResult with authorization_url for redirect
        """
        pass
    
    @abstractmethod
    def verify_payment(self, reference: str) -> PaymentResult:
        """
        Verify a payment transaction.
        
        Args:
            reference: Transaction reference
            
        Returns:
            PaymentResult with payment status and details
        """
        pass
    
    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify webhook signature for security.
        
        Args:
            payload: Raw request body bytes
            signature: Signature from request headers
            
        Returns:
            True if signature is valid
        """
        pass
    
    def generate_reference(self, prefix: str = 'PAY') -> str:
        """Generate a unique payment reference."""
        import uuid
        from django.utils import timezone
        
        timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
        unique = uuid.uuid4().hex[:8].upper()
        return f"{prefix}-{timestamp}-{unique}"
