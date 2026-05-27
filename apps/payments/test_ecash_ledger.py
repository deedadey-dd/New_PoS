from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from decimal import Decimal
import json

from apps.core.models import Tenant, Location, Role
from apps.customers.models import Customer, CustomerTransaction
from apps.payments.models import ECashLedger, PaymentProviderConfig

User = get_user_model()

class AccountantECashPaymentTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant", currency="GHS")
        
        # Create Roles
        self.accountant_role, _ = Role.objects.get_or_create(name="ACCOUNTANT", defaults={"description": "Accountant"})
        
        # Create Locations
        self.hq = Location.objects.create(tenant=self.tenant, name="HQ", location_type="HQ")
        self.shop = Location.objects.create(tenant=self.tenant, name="Test Shop", location_type="SHOP")
        
        # Create Accountant User
        self.accountant = User.objects.create_user(
            email="accountant@test.com",
            password="password",
            tenant=self.tenant,
            role=self.accountant_role,
            location=self.hq,
            first_name="Acc",
            last_name="Ountant"
        )
        
        # Create Payment Provider Config
        self.provider_config = PaymentProviderConfig.objects.create(
            tenant=self.tenant,
            provider="PAYSTACK",
            nickname="Test Paystack",
            is_active=True
        )
        
        # Create Customer
        self.customer = Customer.objects.create(
            tenant=self.tenant,
            name="John Doe",
            phone="0551234567",
            current_balance=Decimal('500.00'),  # Owes 500
            shop=self.shop
        )

    def test_accountant_payment_on_account_updates_ledger_and_context(self):
        """
        Verify that when an accountant records an E-CASH payment on a customer's account:
        1. A CustomerTransaction is created
        2. An ECashLedger entry is created showing the payment
        3. The accountant's ecash_balance in context processors includes this payment
        """
        self.client.force_login(self.accountant)
        
        # 1. Accountant records an E-Cash payment of 200
        response = self.client.post(reverse('customers:customer_payment', args=[self.customer.pk]), {
            'amount': '200.00',
            'payment_method': 'ECASH',
            'provider_config': self.provider_config.pk,
            'description': 'E-CASH Payment on Account'
        })
        
        self.assertEqual(response.status_code, 302)
        
        # Verify CustomerTransaction
        self.assertEqual(CustomerTransaction.objects.count(), 1)
        txn = CustomerTransaction.objects.first()
        self.assertEqual(txn.amount, Decimal('200.00'))
        self.assertEqual(txn.transaction_type, 'CREDIT')
        self.assertIn('ECASH', txn.description)
        
        # Verify ECashLedger
        self.assertEqual(ECashLedger.objects.count(), 1)
        ledger = ECashLedger.objects.first()
        self.assertEqual(ledger.amount, Decimal('200.00'))
        self.assertEqual(ledger.transaction_type, 'PAYMENT')
        self.assertEqual(ledger.reference_type, 'Payment')
        self.assertEqual(ledger.provider_config, self.provider_config)
        # Since it's a customer payment without a sale, the ledger should inherit the customer's shop or accountant's location
        
        # Verify Context Processor for accountant
        response = self.client.get(reverse('accounting:accountant_dashboard'))  # Any view to trigger context processor
        self.assertEqual(response.status_code, 200)
        
        # The ecash_balance should now be 200.00
        ecash_balance = response.context.get('ecash_balance', Decimal('0'))
        self.assertEqual(ecash_balance, Decimal('200.00'))
