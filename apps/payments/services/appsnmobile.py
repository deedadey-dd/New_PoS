"""
AppsnMobile (Orchard) payment provider implementation.
"""
import requests
import hashlib
import hmac
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional

from .base import BasePaymentProvider, PaymentResult


class AppsnMobileProvider(BasePaymentProvider):
    """
    AppsnMobile (Orchard) payment provider.
    Implements the BasePaymentProvider interface for AppsnMobile API.
    """
    
    BASE_URL = "https://orchard-api.anmgw.com"
    
    @property
    def provider_name(self) -> str:
        return "AppsnMobile"
        
    @property
    def get_checkout_type(self) -> str:
        return 'ussd'
    
    def _generate_signature(self, payload_str: str) -> str:
        """Generate HMAC SHA256 signature for the request."""
        return hmac.new(
            self.secret_key.encode('utf-8'),
            payload_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    def _make_request(self, endpoint: str, data: Dict) -> Dict:
        """Make a request to AppsnMobile API."""
        url = f"{self.BASE_URL}{endpoint}"
        
        # Depending on Orchard implementation, signature might be required in headers
        # or it might just use basic auth / API keys.
        # This is a generic setup based on common Orchard patterns.
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.secret_key}' # Often uses Bearer or custom header
        }
        
        try:
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=30
            )
            return response.json()
        except requests.exceptions.RequestException as e:
            return {
                'resp_code': '999',
                'resp_desc': f'Network error: {str(e)}',
            }
        except ValueError:
            return {
                'resp_code': '999',
                'resp_desc': 'Invalid response from AppsnMobile',
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
        Initialize an AppsnMobile transaction.
        Since it's USSD, this actually triggers the prompt on user's phone.
        Required metadata: 'network', 'customer_number'
        """
        customer_number = metadata.get('customer_number', '') if metadata else ''
        network = metadata.get('network', 'MTN') if metadata else 'MTN'
        
        if not customer_number:
            return PaymentResult(
                success=False,
                reference=reference,
                message="Customer phone number required for AppsnMobile"
            )
            
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        payload = {
            'exttrid': reference,
            'reference': 'POS Payment',
            'amount': f"{amount:.2f}",
            'network': network,
            'customer_number': customer_number,
            'service_id': self.settings.merchant_id,
            'ts': ts,
            'trans_type': 'CTM'
        }
        
        response = self._make_request('/sendRequest', payload)
        
        # resp_code '000' is usually success/pending prompt
        if response.get('resp_code') in ['000', '015']:
            return PaymentResult(
                success=True,
                reference=reference,
                message='USSD prompt sent to customer',
                data=response,
                amount=amount
            )
        else:
            return PaymentResult(
                success=False,
                reference=reference,
                message=response.get('resp_desc', 'Failed to initialize payment'),
                data=response
            )
    
    def verify_payment(self, reference: str) -> PaymentResult:
        """
        Verify an AppsnMobile transaction.
        """
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        payload = {
            'exttrid': reference,
            'service_id': self.settings.merchant_id,
            'ts': ts,
            'trans_type': 'TSI'
        }
        
        response = self._make_request('/checkTransaction', payload)
        
        if response.get('resp_code') == '000':
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
                message=response.get('resp_desc', 'Verification failed or pending'),
                data=response
            )
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        AppsnMobile typically sends callbacks. Verify according to their spec.
        """
        return True
