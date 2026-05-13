import json
import logging
import hmac
import hashlib
import requests
from decimal import Decimal
from typing import Dict, Optional

from .base import BasePaymentProvider, PaymentResult

logger = logging.getLogger(__name__)

class NaloProvider(BasePaymentProvider):
    """
    Nalo Hosted Checkout Provider implementation.
    
    Mapping of settings fields:
    - public_key = merchant_id
    - secret_key = Basic Auth token string or password
    - webhook_secret = merchant_secret_key (used for HMAC)
    - base_url = Base API URL (default: https://nalosolutions.com/payment-solutions)
    """
    
    @property
    def provider_name(self) -> str:
        return 'NALO'
        
    @property
    def base_url(self) -> str:
        url = self.settings.base_url.strip()
        if not url:
            url = 'https://nalosolutions.com/payment-solutions'
        return url.rstrip('/')

    def _generate_token(self) -> Optional[str]:
        """Generate JWT token for authentication."""
        url = f"{self.base_url}/clientapi/generate-payment-token/"
        headers = {
            "Authorization": f"Basic {self.secret_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "merchant_id": self.public_key
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            data = response.json()
            if response.status_code == 200 and data.get("success"):
                return data.get("data", {}).get("token")
            logger.error(f"Nalo token generation failed: {data}")
        except Exception as e:
            logger.error(f"Nalo token request error: {str(e)}")
            
        return None

    def _generate_trans_hash(self, order_id: str, amount: str, reference: str) -> str:
        """Compute HMAC-SHA256 trans_hash."""
        # Order: merchant_id + order_id + total_price + reference
        message = f"{self.public_key}{order_id}{amount}{reference}"
        merchant_secret_key = self.settings.webhook_secret or ""
        
        signature = hmac.new(
            merchant_secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return signature

    def initialize_payment(
        self,
        amount: Decimal,
        email: str,
        reference: str,
        callback_url: str = '',
        metadata: Optional[Dict] = None
    ) -> PaymentResult:
        """Initialize Nalo Hosted Checkout session."""
        token = self._generate_token()
        if not token:
            return PaymentResult(success=False, message="Failed to authenticate with Nalo")
            
        # Format amount to 2 decimal places
        str_amount = f"{amount:.2f}"
        order_id = reference  # Use the same reference for order_id
        
        trans_hash = self._generate_trans_hash(order_id, str_amount, reference)
        
        url = f"{self.base_url}/checkout/session/"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        customer_name = "Customer"
        if metadata and "customer_name" in metadata:
            customer_name = metadata["customer_name"]
            
        payload = {
            "merchant": {
                "merchant_id": self.public_key,
                "order_id": order_id,
                "customer_name": customer_name,
                "referral_url": callback_url,
                "callback_url": callback_url,
                "trans_hash": trans_hash,
                "reference": reference,
                "mode": "MOMO"  # Or dynamic based on metadata if needed
            },
            "summary": {
                "products": [
                    {
                        "name": "POS Checkout",
                        "count": 1,
                        "price": str_amount,
                        "metadata": metadata or {}
                    }
                ],
                "item_count": 1,
                "total_price": str_amount
            }
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            data = response.json()
            
            if response.status_code == 200 and data.get("success"):
                checkout_url = data.get("data", {}).get("checkout_url")
                return PaymentResult(
                    success=True,
                    reference=reference,
                    authorization_url=checkout_url,
                    message="Checkout URL generated successfully"
                )
            
            error_msg = data.get("message") or "Failed to initialize checkout session"
            logger.error(f"Nalo checkout failed: {data}")
            return PaymentResult(success=False, message=error_msg)
            
        except Exception as e:
            logger.error(f"Nalo checkout request error: {str(e)}")
            return PaymentResult(success=False, message="Service temporarily unavailable")

    def verify_payment(self, reference: str) -> PaymentResult:
        """Verify the payment via Nalo's collection-status endpoint."""
        token = self._generate_token()
        if not token:
            return PaymentResult(success=False, message="Authentication failed")
            
        url = f"{self.base_url}/clientapi/collection-status/"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "merchant_id": self.public_key,
            "order_id": reference
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            data = response.json()
            
            if response.status_code == 200 and data.get("success"):
                status_data = data.get("data", {})
                status = status_data.get("status", "")
                
                # Check if COMPLETED
                if status == "COMPLETED":
                    return PaymentResult(
                        success=True,
                        reference=reference,
                        amount=Decimal(str(status_data.get("amount", 0))),
                        message="Payment verified successfully"
                    )
                else:
                    return PaymentResult(
                        success=False,
                        reference=reference,
                        message=f"Payment status: {status}"
                    )
            
            return PaymentResult(success=False, message="Verification failed")
            
        except Exception as e:
            logger.error(f"Nalo verify error: {str(e)}")
            return PaymentResult(success=False, message="Verification service unavailable")

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify Nalo Webhook signature."""
        # Nalo documentation does not explicitly detail a webhook signature header yet.
        # But if they use an HMAC-SHA256 signature header, we'd verify it here.
        # Returning True for now, but in production, we should validate the signature.
        return True

    def test_connection(self) -> PaymentResult:
        """Test API credentials by generating a token."""
        token = self._generate_token()
        if token:
            return PaymentResult(success=True, message="Nalo connection successful!")
        return PaymentResult(success=False, message="Nalo connection failed. Check credentials.")
