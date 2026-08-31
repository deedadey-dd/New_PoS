from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.contrib.messages.storage.fallback import FallbackStorage
from apps.core.models import Tenant, Location, User, Role
from apps.sales.models import Shift, Sale, SaleItem
from apps.customers.models import Customer, CustomerTransaction
from apps.accounting.models import CashTransfer, BankTransfer
from apps.accounting.views import BankTransferCreateView
from apps.accounting.forms import BankTransferForm
from decimal import Decimal
from django.db.models import Sum

class CashierBankTransferTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Test Tenant', use_strict_sales_workflow=True)
        self.shop = Location.objects.create(tenant=self.tenant, name='Test Shop', location_type='SHOP', is_active=True)
        
        # Create Roles
        self.cashier_role, _ = Role.objects.get_or_create(name='SHOP_CASHIER')
        self.accountant_role, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        
        # Create Users
        self.cashier = User.objects.create_user(
            email='cashier@test.com',
            password='password123',
            tenant=self.tenant,
            location=self.shop,
            role=self.cashier_role,
            is_active=True
        )
        self.accountant = User.objects.create_user(
            email='accountant@test.com',
            password='password123',
            tenant=self.tenant,
            location=self.shop,
            role=self.accountant_role,
            is_active=True
        )

        self.factory = RequestFactory()

    def test_bank_transfer_access_for_cashier_in_strict_mode(self):
        """Cashier can access bank transfer view when strict mode is ON"""
        request = self.factory.get(reverse('accounting:bank_transfer_create'))
        request.user = self.cashier
        setattr(request, 'session', 'session')
        messages = FallbackStorage(request)
        setattr(request, '_messages', messages)

        view = BankTransferCreateView.as_view()
        response = view(request)
        self.assertEqual(response.status_code, 200)

    def test_bank_transfer_form_fund_source_choices_for_cashier(self):
        """Cashier should only have CASH in fund_source choices"""
        form = BankTransferForm(user=self.cashier)
        self.assertEqual(form.fields['fund_source'].choices, [('CASH', 'Cash')])

    def test_bank_transfer_form_clean_amount_for_cashier(self):
        """BankTransferForm should correctly calculate cash on hand for cashier"""
        # Give cashier some cash from a CashTransfer (Float)
        CashTransfer.objects.create(
            tenant=self.tenant,
            from_user=self.accountant,
            to_user=self.cashier,
            from_location=self.shop,
            to_location=self.shop,
            transfer_type='DEPOSIT',
            amount=Decimal('500.00'),
            status='CONFIRMED',
            confirmed_by=self.cashier
        )

        form_data = {
            'amount': '300.00',
            'fund_source': 'CASH',
            'teller_name': 'Teller 1',
        }
        form = BankTransferForm(data=form_data, user=self.cashier)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['amount'], Decimal('300.00'))

        # Try to withdraw more than available
        form_data_invalid = {
            'amount': '600.00',
            'fund_source': 'CASH',
            'teller_name': 'Teller 1',
        }
        form_invalid = BankTransferForm(data=form_data_invalid, user=self.cashier)
        self.assertFalse(form_invalid.is_valid(), form_invalid.errors)
        self.assertIn('amount', form_invalid.errors)

    def test_strict_workflow_attendant_cash_sale_reflected_and_bankable(self):
        """Attendants accepting cash sales in strict mode see cash on hand and can transfer to bank"""
        attendant_role, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')
        attendant = User.objects.create_user(
            email='attendant@test.com',
            password='password123',
            tenant=self.tenant,
            location=self.shop,
            role=attendant_role,
            is_active=True
        )

        # Attendant makes a cash sale (acting as cashier/attendant)
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=attendant,
            cashier=attendant,
            payment_method='CASH',
            total=Decimal('250.00'),
            amount_paid=Decimal('250.00'),
            status='PENDING_DISPATCH'
        )

        # Check context processor navbar calculation
        from apps.core.context_processors import tenant_context
        request = self.factory.get('/')
        request.user = attendant
        ctx = tenant_context(request)
        self.assertEqual(ctx['cash_on_hand'], Decimal('250.00'))

        # Check BankTransferForm validation
        form_data = {
            'amount': '200.00',
            'fund_source': 'CASH',
            'teller_name': 'Teller 1',
        }
        form = BankTransferForm(data=form_data, user=attendant)
        self.assertTrue(form.is_valid(), form.errors)

    def test_strict_workflow_cashier_receives_invoice_payment_reflected_in_cash_on_hand(self):
        """When cashier receives cash for invoice created by attendant, cash on hand is assigned to cashier"""
        attendant_role, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')
        attendant = User.objects.create_user(
            email='attendant2@test.com',
            password='password123',
            tenant=self.tenant,
            location=self.shop,
            role=attendant_role,
            is_active=True
        )

        # Invoice created by attendant (pending)
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=attendant,
            cashier=None,
            payment_method='PENDING_INVOICE',
            total=Decimal('400.00'),
            amount_paid=Decimal('0.00'),
            status='PENDING'
        )

        # Cashier completes payment
        sale.complete(Decimal('400.00'), payment_method='CASH', cashier=self.cashier)

        # Cashier's cash on hand should be 400.00
        from apps.core.context_processors import tenant_context
        request = self.factory.get('/')
        request.user = self.cashier
        ctx_cashier = tenant_context(request)
        self.assertEqual(ctx_cashier['cash_on_hand'], Decimal('400.00'))

        # Attendant's cash on hand should be 0.00 (attendant did not take the cash)
        request.user = attendant
        ctx_attendant = tenant_context(request)
        self.assertEqual(ctx_attendant['cash_on_hand'], Decimal('0.00'))

        # Cashier can transfer 400 to bank
        form_data = {
            'amount': '400.00',
            'fund_source': 'CASH',
            'teller_name': 'Teller Cashier',
        }
        form = BankTransferForm(data=form_data, user=self.cashier)
        self.assertTrue(form.is_valid(), form.errors)

