"""
Hubtel payment provider implementation stub.
"""
from decimal import Decimal
from typing import Dict, Optional

from .base import BasePaymentProvider, PaymentResult


class HubtelProvider(BasePaymentProvider):
    """
    Hubtel payment provider.
    Implements the BasePaymentProvider interface for Hubtel API.
    """
    
    BASE_URL = "https://api.hubtel.com/v1"  # Placeholder URL
    
    @property
    def provider_name(self) -> str:
        return "Hubtel"
    
    def initialize_payment(
        self,
        amount: Decimal,
        email: str,
        reference: str,
        callback_url: str = '',
        metadata: Optional[Dict] = None
    ) -> PaymentResult:
        """
        Initialize a Hubtel transaction.
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
        """Verify a Hubtel transaction by reference. (Stub)"""
        return PaymentResult(
            success=False,
            reference=reference,
            message="Not implemented yet"
        )
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify Hubtel webhook signature. (Stub)"""
        return False
