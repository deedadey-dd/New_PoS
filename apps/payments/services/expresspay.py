"""
ExpressPay payment provider implementation.
"""
import requests
from decimal import Decimal
from typing import Dict, Optional

from .base import BasePaymentProvider, PaymentResult


class ExpressPayProvider(BasePaymentProvider):
    """
    ExpressPay payment provider.
    Implements the BasePaymentProvider interface for ExpressPay API.
    """
    
    # Use sandbox or live URL based on settings
    @property
    def BASE_URL(self) -> str:
        if self.test_mode:
            return "https://sandbox.expresspaygh.com/api"
        return "https://expresspaygh.com/api"
    
    @property
    def provider_name(self) -> str:
        return "ExpressPay"
        
    @property
    def get_checkout_type(self) -> str:
        return 'redirect'
    
    def _make_request(self, endpoint: str, data: Dict) -> Dict:
        """Make a request to ExpressPay API."""
        url = f"{self.BASE_URL}{endpoint}"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        
        try:
            response = requests.post(
                url,
                headers=headers,
                data=data,
                timeout=30
            )
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                'status': 0,
                'message': f'Network error: {str(e)}',
            }
        except ValueError:
            return {
                'status': 0,
                'message': 'Invalid response from ExpressPay',
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
        Initialize an ExpressPay transaction.
        """
        # ExpressPay requires these fields; we'll provide dummy values if missing
        first_name = "Customer"
        last_name = "Name"
        phone = "0000000000"
        
        if metadata:
            if 'first_name' in metadata: first_name = metadata['first_name']
            if 'last_name' in metadata: last_name = metadata['last_name']
            if 'phone' in metadata: phone = metadata['phone']
            
        payload = {
            'merchant-id': self.settings.merchant_id,
            'api-key': self.secret_key,
            'firstname': first_name,
            'lastname': last_name,
            'email': email,
            'phonenumber': phone,
            'currency': 'GHS',
            'amount': f"{amount:.2f}",
            'order-id': reference,
            'redirect-url': callback_url or self.settings.callback_url,
            'post-url': self.settings.callback_url
        }
        
        response = self._make_request('/submit.php', payload)
        
        if response.get('status') == 1:
            token = response.get('token')
            auth_url = f"{self.BASE_URL}/checkout.php?token={token}"
            
            return PaymentResult(
                success=True,
                reference=reference,
                message='Payment initialized',
                data={'token': token, **response},
                amount=amount,
                authorization_url=auth_url
            )
        else:
            return PaymentResult(
                success=False,
                reference=reference,
                message=response.get('message', 'Failed to initialize payment'),
                data=response
            )
    
    def verify_payment(self, reference: str, token: str = '') -> PaymentResult:
        """
        Verify an ExpressPay transaction.
        ExpressPay's query API requires the token, not the order-id.
        If token is not provided, we can't reliably verify via API.
        """
        if not token:
            return PaymentResult(
                success=False,
                reference=reference,
                message="ExpressPay verification requires the token."
            )
            
        payload = {
            'merchant-id': self.settings.merchant_id,
            'api-key': self.secret_key,
            'token': token
        }
        
        response = self._make_request('/query.php', payload)
        
        # result: 1 is success
        if response.get('result') == 1:
            return PaymentResult(
                success=True,
                reference=reference,
                message='Payment successful',
                data=response,
                amount=Decimal(response.get('amount', '0')) if 'amount' in response else Decimal('0')
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
        ExpressPay doesn't use standard webhook signatures.
        They send a POST with token/result to post-url, and we must query it.
        We'll handle this at the webhook view level.
        """
        return True
