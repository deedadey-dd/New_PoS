"""
AppsNMobile payment provider implementation stub.
"""
from decimal import Decimal
from typing import Dict, Optional

from .base import BasePaymentProvider, PaymentResult


class AppsNMobileProvider(BasePaymentProvider):
    """
    AppsNMobile payment provider.
    Implements the BasePaymentProvider interface for AppsNMobile API.
    """
    
    BASE_URL = "https://api.appsnmobile.com/v1"  # Placeholder URL
    
    @property
    def provider_name(self) -> str:
        return "AppsNMobile"
    
    def initialize_payment(
        self,
        amount: Decimal,
        email: str,
        reference: str,
        callback_url: str = '',
        metadata: Optional[Dict] = None
    ) -> PaymentResult:
        """
        Initialize an AppsNMobile transaction.
        (Stub implementation)
        """
        payload = {
            'amount': float(amount),
            'reference': reference,
            'customer_email': email,
        }
        
        return PaymentResult(
            success=True,
            reference=reference,
            message='Payment initialized (stub)',
            data=payload,
            amount=amount
        )
    
    def verify_payment(self, reference: str) -> PaymentResult:
        """Verify an AppsNMobile transaction by reference. (Stub)"""
        return PaymentResult(
            success=False,
            reference=reference,
            message="Not implemented yet"
        )
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify AppsNMobile webhook signature. (Stub)"""
        return False
