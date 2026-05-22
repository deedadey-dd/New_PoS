from decimal import Decimal
from django.test import TestCase
from apps.core.models import Tenant, Role, User, Location
from apps.accounting.models import CashTransfer
from apps.accounting.forms import CashTransferForm

class CashTransferTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant", subdomain="test")
        # Ensure roles exist
        self.role_cashier, _ = Role.objects.get_or_create(name="SHOP_CASHIER")
        self.role_accountant, _ = Role.objects.get_or_create(name="ACCOUNTANT")
        
        self.cashier = User.objects.create_user(
            username="cashier@test.com",
            email="cashier@test.com",
            password="password",
            tenant=self.tenant,
            role=self.role_cashier
        )
        self.accountant = User.objects.create_user(
            username="accountant@test.com",
            email="accountant@test.com",
            password="password",
            tenant=self.tenant,
            role=self.role_accountant
        )
        
    def test_cash_transfer_creation(self):
        transfer = CashTransfer.objects.create(
            tenant=self.tenant,
            amount=Decimal('50.00'),
            transfer_type='DEPOSIT',
            destination='BANK',
            from_user=self.cashier,
            to_user=self.accountant
        )
        self.assertEqual(transfer.destination, 'BANK')
        self.assertEqual(transfer.amount, Decimal('50.00'))
        
    def test_cash_transfer_form_setup(self):
        form = CashTransferForm(user=self.cashier)
        self.assertEqual(list(form.fields['to_user'].queryset), [self.accountant])
        self.assertEqual(form.fields['destination'].initial, 'BANK')
