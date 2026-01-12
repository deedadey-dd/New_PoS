"""
Paystack payment provider implementation.
https://paystack.com/docs/api/
"""
import hashlib
import hmac
import json
import requests
from decimal import Decimal
from typing import Dict, Optional

from .base import BasePaymentProvider, PaymentResult


class PaystackProvider(BasePaymentProvider):
    """
    Paystack payment provider.
    Implements the BasePaymentProvider interface for Paystack API.
    """
    
    BASE_URL = "https://api.paystack.co"
    
    @property
    def provider_name(self) -> str:
        return "Paystack"
    
    def _get_headers(self) -> Dict:
        """Get headers for Paystack API requests."""
        return {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json',
        }
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """Make a request to Paystack API."""
        url = f"{self.BASE_URL}{endpoint}"
        
        try:
            if method.upper() == 'GET':
                response = requests.get(url, headers=self._get_headers(), timeout=30)
            else:
                response = requests.post(
                    url,
                    headers=self._get_headers(),
                    json=data,
                    timeout=30
                )
            
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                'status': False,
                'message': f'Network error: {str(e)}',
            }
        except json.JSONDecodeError:
            return {
                'status': False,
                'message': 'Invalid response from Paystack',
            }
    
    def initialize_payment(
        self,
        amount: Decimal,
        email: str,
        reference: str,
        callback_url: str = '',
        metadata: Optional[Dict] = None
    ) -> PaymentResult:
        """
        Initialize a Paystack transaction.
        
        Note: Paystack expects amount in kobo/pesewas (minor currency units)
        """
        # Convert to minor units (kobo/pesewas)
        amount_minor = int(amount * 100)
        
        payload = {
            'amount': amount_minor,
            'email': email,
            'reference': reference,
            'currency': 'GHS',  # Ghana Cedis
        }
        
        if callback_url:
            payload['callback_url'] = callback_url
        
        if metadata:
            payload['metadata'] = metadata
        
        response = self._make_request('POST', '/transaction/initialize', payload)
        
        if response.get('status'):
            data = response.get('data', {})
            return PaymentResult(
                success=True,
                reference=data.get('reference', reference),
                message='Payment initialized',
                data=data,
                amount=amount,
                authorization_url=data.get('authorization_url', '')
            )
        else:
            return PaymentResult(
                success=False,
                reference=reference,
                message=response.get('message', 'Failed to initialize payment'),
                data=response
            )
    
    def verify_payment(self, reference: str) -> PaymentResult:
        """Verify a Paystack transaction by reference."""
        response = self._make_request('GET', f'/transaction/verify/{reference}')
        
        if response.get('status'):
            data = response.get('data', {})
            payment_status = data.get('status', '')
            
            if payment_status == 'success':
                # Convert amount back from kobo to major units
                amount_kobo = data.get('amount', 0)
                amount = Decimal(amount_kobo) / 100
                
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
                    message=f"Payment {payment_status}",
                    data=data
                )
        else:
            return PaymentResult(
                success=False,
                reference=reference,
                message=response.get('message', 'Verification failed'),
                data=response
            )
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify Paystack webhook signature.
        
        Paystack sends signature in X-Paystack-Signature header.
        It's a HMAC SHA512 hash of the request body using your secret key.
        """
        if not self.settings.webhook_secret:
            # If no webhook secret set, use secret key
            secret = self.secret_key
        else:
            secret = self.settings.webhook_secret
        
        if not secret:
            return False
        
        computed_signature = hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha512
        ).hexdigest()
        
        return hmac.compare_digest(computed_signature, signature)
    
    def get_banks(self) -> PaymentResult:
        """Get list of banks for bank transfers."""
        response = self._make_request('GET', '/bank?country=ghana')
        
        if response.get('status'):
            return PaymentResult(
                success=True,
                message='Banks retrieved',
                data={'banks': response.get('data', [])}
            )
        else:
            return PaymentResult(
                success=False,
                message=response.get('message', 'Failed to get banks')
            )
    
    def test_connection(self) -> PaymentResult:
        """Test if the API credentials are valid."""
        response = self._make_request('GET', '/balance')
        
        if response.get('status'):
            return PaymentResult(
                success=True,
                message='Connection successful',
                data=response.get('data', [])
            )
        else:
            return PaymentResult(
                success=False,
                message=response.get('message', 'Connection failed')
            )


def get_payment_provider(tenant):
    """
    Factory function to get the active payment provider for a tenant.
    
    Args:
        tenant: Tenant model instance
        
    Returns:
        Payment provider instance or None
    """
    from apps.payments.models import PaymentProviderSettings
    
    settings = PaymentProviderSettings.objects.filter(
        tenant=tenant,
        is_active=True
    ).first()
    
    if not settings:
        return None
    
    if settings.provider == 'PAYSTACK':
        return PaystackProvider(settings)
    # Add more providers here as they're implemented
    # elif settings.provider == 'FLUTTERWAVE':
    #     return FlutterwaveProvider(settings)
    
    return None
