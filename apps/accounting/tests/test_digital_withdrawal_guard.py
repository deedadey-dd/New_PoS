from django.test import TestCase
from django.urls import reverse
from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Sale
from apps.customers.models import Customer, CustomerTransaction
from apps.payments.models import ECashLedger, PaymentProviderConfig
from decimal import Decimal

class DigitalWithdrawalGuardTests(TestCase):
    """
    Tests for the server-side guards that prevent accountants from withdrawing
    more Momo or E-Cash funds than what is actually 'Confirmed and Available'.
    """
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Guard Test Tenant")
        self.accountant_role, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        self.attendant_role, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')

        self.shop = Location.objects.create(
            name="Main Shop", tenant=self.tenant, location_type='SHOP'
        )
        self.accountant = User.objects.create_user(
            email="accountant@test.com", password="password123",
            tenant=self.tenant, role=self.accountant_role
        )
        self.attendant = User.objects.create_user(
            email="attendant@test.com", password="password123",
            tenant=self.tenant, role=self.attendant_role, location=self.shop
        )

        self.provider = PaymentProviderConfig.objects.create(
            tenant=self.tenant, provider='PAYSTACK', is_active=True
        )

    def _create_momo_sales(self, confirmed_amount, unconfirmed_amount):
        # Confirmed
        if confirmed_amount > 0:
            Sale.objects.create(
                tenant=self.tenant, shop=self.shop, attendant=self.attendant,
                sale_number="SL-M-C", total=confirmed_amount, amount_paid=confirmed_amount,
                status='COMPLETED', payment_method='MOMO', is_accountant_confirmed=True
            )
        # Unconfirmed
        if unconfirmed_amount > 0:
            Sale.objects.create(
                tenant=self.tenant, shop=self.shop, attendant=self.attendant,
                sale_number="SL-M-U", total=unconfirmed_amount, amount_paid=unconfirmed_amount,
                status='COMPLETED', payment_method='MOMO', is_accountant_confirmed=False
            )

    def _create_ecash_sales(self, confirmed_amount, unconfirmed_amount):
        if confirmed_amount > 0:
            sale_c = Sale.objects.create(
                tenant=self.tenant, shop=self.shop, attendant=self.attendant,
                sale_number="SL-E-C", total=confirmed_amount, amount_paid=confirmed_amount,
                status='COMPLETED', payment_method='ECASH', is_accountant_confirmed=True
            )
            ECashLedger.record_payment(
                tenant=self.tenant, amount=confirmed_amount, sale=sale_c,
                shop=self.shop, user=self.attendant, provider_config=self.provider
            )
        if unconfirmed_amount > 0:
            sale_u = Sale.objects.create(
                tenant=self.tenant, shop=self.shop, attendant=self.attendant,
                sale_number="SL-E-U", total=unconfirmed_amount, amount_paid=unconfirmed_amount,
                status='COMPLETED', payment_method='ECASH', is_accountant_confirmed=False
            )
            ECashLedger.record_payment(
                tenant=self.tenant, amount=unconfirmed_amount, sale=sale_u,
                shop=self.shop, user=self.attendant, provider_config=self.provider
            )

    def test_momo_withdrawal_guard(self):
        """Accountant can only withdraw confirmed Momo funds."""
        self._create_momo_sales(confirmed_amount=Decimal('100.00'), unconfirmed_amount=Decimal('50.00'))
        self.client.force_login(self.accountant)
        
        # Try to withdraw 120 (fails because only 100 is confirmed)
        url = reverse('accounting:shop_momo_withdraw', kwargs={'shop_id': self.shop.id})
        response = self.client.post(url, {'amount': '120.00', 'notes': 'Test'})
        
        # Should redirect back to list and NOT create withdrawal
        self.assertRedirects(response, reverse('accounting:shop_momo_list'))
        from apps.accounting.models import DigitalFundWithdrawal
        self.assertEqual(DigitalFundWithdrawal.objects.filter(tenant=self.tenant).count(), 0)
        
        # Try to withdraw 90 (succeeds)
        response = self.client.post(url, {'amount': '90.00', 'notes': 'Valid'})
        self.assertRedirects(response, reverse('accounting:shop_momo_list'))
        self.assertEqual(DigitalFundWithdrawal.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(DigitalFundWithdrawal.objects.filter(tenant=self.tenant).first().amount, Decimal('90.00'))

    def test_ecash_withdrawal_guard(self):
        """Accountant can only withdraw confirmed E-Cash funds for a provider."""
        self._create_ecash_sales(confirmed_amount=Decimal('200.00'), unconfirmed_amount=Decimal('100.00'))
        self.client.force_login(self.accountant)
        
        # Try to withdraw 250 (fails)
        url = reverse('accounting:shop_ecash_withdraw', kwargs={'shop_id': self.shop.id})
        response = self.client.post(url, {
            'amount': '250.00', 'notes': 'Test', 'provider_config_id': self.provider.id
        })
        
        # Should redirect and NOT create withdrawal
        self.assertRedirects(response, reverse('accounting:shop_ecash_list'))
        from apps.accounting.models import DigitalFundWithdrawal
        self.assertEqual(DigitalFundWithdrawal.objects.filter(tenant=self.tenant).count(), 0)
        
        # Try to withdraw 200 (succeeds)
        response = self.client.post(url, {
            'amount': '200.00', 'notes': 'Valid', 'provider_config_id': self.provider.id
        })
        self.assertRedirects(response, reverse('accounting:shop_ecash_list'))
        self.assertEqual(DigitalFundWithdrawal.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(DigitalFundWithdrawal.objects.filter(tenant=self.tenant).first().amount, Decimal('200.00'))
