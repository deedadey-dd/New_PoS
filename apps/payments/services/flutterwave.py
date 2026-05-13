"""
Flutterwave payment provider implementation.
"""
from decimal import Decimal
from typing import Dict, Optional
import hashlib
import hmac

from .base import BasePaymentProvider, PaymentResult


class FlutterwaveProvider(BasePaymentProvider):
    """
    Flutterwave payment provider.
    Implements the BasePaymentProvider interface for Flutterwave API.
    """
    
    BASE_URL = "https://api.flutterwave.com/v3"
    
    @property
    def provider_name(self) -> str:
        return "Flutterwave"
    
    def initialize_payment(
        self,
        amount: Decimal,
        email: str,
        reference: str,
        callback_url: str = '',
        metadata: Optional[Dict] = None
    ) -> PaymentResult:
        """
        Initialize a Flutterwave transaction.
        For now, this returns a payload that can be used with Flutterwave's inline JS.
        """
        payload = {
            'public_key': self.public_key,
            'tx_ref': reference,
            'amount': float(amount),
            'currency': 'GHS',
            'payment_options': 'card, mobilemoneyghana',
            'customer': {
                'email': email,
            },
            'customizations': {
                'title': 'Payment',
                'description': 'Payment for items'
            }
        }
        
        if callback_url:
            payload['redirect_url'] = callback_url
            
        if metadata:
            payload['meta'] = metadata

        return PaymentResult(
            success=True,
            reference=reference,
            message='Payment initialized',
            data=payload,
            amount=amount
        )
    
    def verify_payment(self, reference: str) -> PaymentResult:
        """Verify a Flutterwave transaction by reference."""
        import requests
        
        url = f"{self.BASE_URL}/transactions/verify_by_reference?tx_ref={reference}"
        headers = {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json',
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            data = response.json()
            
            if data.get('status') == 'success':
                tx_data = data.get('data', {})
                if tx_data.get('status') == 'successful':
                    amount = Decimal(str(tx_data.get('amount', 0)))
                    return PaymentResult(
                        success=True,
                        reference=reference,
                        message='Payment successful',
                        data=data,
                        amount=amount
                    )
                else:
                    return PaymentResult(
                        success=False,
                        reference=reference,
                        message=f"Payment status: {tx_data.get('status')}",
                        data=data
                    )
            else:
                return PaymentResult(
                    success=False,
                    reference=reference,
                    message=data.get('message', 'Verification failed'),
                    data=data
                )
        except Exception as e:
            return PaymentResult(
                success=False,
                reference=reference,
                message=f'Network error: {str(e)}'
            )
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify Flutterwave webhook signature.
        Flutterwave sends the secret hash in the verif-hash header.
        """
        secret_hash = self.settings.webhook_secret or self.secret_key
        return signature == secret_hash

