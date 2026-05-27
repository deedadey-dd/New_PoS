"""
Nalopay payment provider implementation.
"""
import requests
from decimal import Decimal
from typing import Dict, Optional

from .base import BasePaymentProvider, PaymentResult


class NalopayProvider(BasePaymentProvider):
    """
    Nalopay payment provider.
    Implements the BasePaymentProvider interface for Nalopay API.
    """
    
    # Update with actual Nalopay base URL
    BASE_URL = "https://api.nalopay.com/v1"
    
    @property
    def provider_name(self) -> str:
        return "Nalopay"
        
    @property
    def get_checkout_type(self) -> str:
        return 'redirect'
    
    def _get_headers(self) -> Dict:
        """Get headers for Nalopay API requests."""
        return {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json',
        }
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """Make a request to Nalopay API."""
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
        except ValueError:
            return {
                'status': False,
                'message': 'Invalid response from Nalopay',
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
        Initialize a Nalopay transaction.
        """
        payload = {
            'amount': float(amount),
            'email': email,
            'reference': reference,
            'currency': 'GHS',
            'callback_url': callback_url or self.settings.callback_url
        }
        
        if metadata:
            payload['metadata'] = metadata
            
        response = self._make_request('POST', '/checkout/initialize', payload)
        
        if response.get('status') or response.get('success'):
            data = response.get('data', {})
            return PaymentResult(
                success=True,
                reference=reference,
                message='Payment initialized',
                data=data,
                amount=amount,
                authorization_url=data.get('checkout_url', '')
            )
        else:
            return PaymentResult(
                success=False,
                reference=reference,
                message=response.get('message', 'Failed to initialize payment'),
                data=response
            )
    
    def verify_payment(self, reference: str) -> PaymentResult:
        """
        Verify a Nalopay transaction.
        """
        response = self._make_request('GET', f'/transaction/verify/{reference}')
        
        if response.get('status') or response.get('success'):
            data = response.get('data', {})
            payment_status = data.get('status', '').lower()
            
            if payment_status == 'successful' or payment_status == 'success':
                return PaymentResult(
                    success=True,
                    reference=reference,
                    message='Payment successful',
                    data=data,
                    amount=Decimal(str(data.get('amount', 0)))
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
        Verify webhook signature.
        """
        # Implement based on Nalopay's actual signature logic
        return True
