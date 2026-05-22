"""
Tests for recently fixed issues:
  1. Digital balance logic via DigitalFundWithdrawal (E-Cash & Momo)
  2. Logout redirect respects `next` query param (Return to Home button)
  3. CashTransfer form - SHOP_ATTENDANT only sees Shop Manager as recipient
"""
from decimal import Decimal

from django.test import TestCase, RequestFactory
from django.urls import reverse

from apps.core.models import Tenant, Location, Role, User
from apps.core.context_processors import tenant_context
from apps.accounting.models import CashTransfer, BankTransfer, DigitalFundWithdrawal
from apps.sales.models import Sale


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_user(email, tenant, role_name, location=None):
    role, _ = Role.objects.get_or_create(name=role_name, defaults={'description': role_name})
    return User.objects.create_user(
        email=email, password='testpass123',
        tenant=tenant, role=role, location=location
    )


def get_context(user):
    """Run the tenant_context context processor for a given user."""
    factory = RequestFactory()
    request = factory.get('/')
    request.user = user
    return tenant_context(request)


# ---------------------------------------------------------------------------
# 1. Digital Balance Logic
# ---------------------------------------------------------------------------

class DigitalFundWithdrawalTests(TestCase):
    """
    Verify E-Cash and Local Momo balances reflect the DigitalFundWithdrawal
    model for shop managers and accountants respectively.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Fix Test Tenant', slug='fix-test-tenant',
            allow_momo_payments=True,
        )
        hq = Location.objects.create(tenant=self.tenant, name='HQ', location_type='HQ')
        self.shop = Location.objects.create(tenant=self.tenant, name='Shop A', location_type='SHOP')

        self.manager = make_user('manager@fix.test', self.tenant, 'SHOP_MANAGER', self.shop)
        self.accountant = make_user('accountant@fix.test', self.tenant, 'ACCOUNTANT', hq)

    # ---- E-Cash -----------------------------------------------------------

    def test_ecash_shop_manager_shows_unwithddrawn_balance(self):
        """Shop Manager sees total E-Cash sales minus any DigitalFundWithdrawals."""
        Sale.objects.create(
            tenant=self.tenant, shop=self.shop, attendant=self.manager,
            total=Decimal('200.00'), amount_paid=Decimal('200.00'),
            status='COMPLETED', payment_method='ECASH',
        )

        # Before withdrawal: manager should see full 200
        ctx = get_context(self.manager)
        self.assertEqual(ctx['ecash_balance'], Decimal('200.00'))

        # Accountant withdraws 120
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant, shop=self.shop, accountant=self.accountant,
            amount=Decimal('120.00'), fund_source='ECASH', notes='Partial withdrawal',
        )

        # Manager now sees 80 (200 - 120)
        ctx = get_context(self.manager)
        self.assertEqual(ctx['ecash_balance'], Decimal('80.00'))

    def test_ecash_accountant_balance_equals_withdrawals_minus_bank(self):
        """Accountant sees total DigitalFundWithdrawals minus BankTransfers."""
        Sale.objects.create(
            tenant=self.tenant, shop=self.shop, attendant=self.manager,
            total=Decimal('300.00'), amount_paid=Decimal('300.00'),
            status='COMPLETED', payment_method='ECASH',
        )

        # No withdrawal yet — accountant sees 0
        ctx = get_context(self.accountant)
        self.assertEqual(ctx['ecash_balance'], Decimal('0.00'))

        # Withdraw 300
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant, shop=self.shop, accountant=self.accountant,
            amount=Decimal('300.00'), fund_source='ECASH', notes='Full withdrawal',
        )

        # Accountant sees 300
        ctx = get_context(self.accountant)
        self.assertEqual(ctx['ecash_balance'], Decimal('300.00'))

        # Bank 200 of it
        BankTransfer.objects.create(
            tenant=self.tenant, accountant=self.accountant,
            amount=Decimal('200.00'), fund_source='ECASH', teller_name='John',
        )

        # Accountant now holds only 100
        ctx = get_context(self.accountant)
        self.assertEqual(ctx['ecash_balance'], Decimal('100.00'))

    # ---- Momo -------------------------------------------------------------

    def test_momo_shop_manager_sees_unwithdrawn_balance(self):
        """Shop Manager sees total Momo sales minus DigitalFundWithdrawals."""
        Sale.objects.create(
            tenant=self.tenant, shop=self.shop, attendant=self.manager,
            total=Decimal('150.00'), amount_paid=Decimal('150.00'),
            status='COMPLETED', payment_method='MOMO',
        )

        ctx = get_context(self.manager)
        self.assertEqual(ctx['momo_balance'], Decimal('150.00'))

        # Withdraw 100
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant, shop=self.shop, accountant=self.accountant,
            amount=Decimal('100.00'), fund_source='MOMO', notes='Partial',
        )

        # Manager sees 50
        ctx = get_context(self.manager)
        self.assertEqual(ctx['momo_balance'], Decimal('50.00'))

    def test_momo_accountant_balance_equals_withdrawals_minus_bank(self):
        """Accountant sees Total Momo Withdrawals minus BankTransfers."""
        Sale.objects.create(
            tenant=self.tenant, shop=self.shop, attendant=self.manager,
            total=Decimal('80.00'), amount_paid=Decimal('80.00'),
            status='COMPLETED', payment_method='MOMO',
        )

        # Withdraw all
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant, shop=self.shop, accountant=self.accountant,
            amount=Decimal('80.00'), fund_source='MOMO', notes='Full',
        )

        ctx = get_context(self.accountant)
        self.assertEqual(ctx['momo_balance'], Decimal('80.00'))

        # Bank 50
        BankTransfer.objects.create(
            tenant=self.tenant, accountant=self.accountant,
            amount=Decimal('50.00'), fund_source='MOMO', teller_name='Jane',
        )

        # Accountant holds 30
        ctx = get_context(self.accountant)
        self.assertEqual(ctx['momo_balance'], Decimal('30.00'))


# ---------------------------------------------------------------------------
# 2. Logout Redirect (Return to Home)
# ---------------------------------------------------------------------------

class LogoutRedirectTests(TestCase):
    """
    Verify that the LogoutView respects the `next` query parameter so
    the demo banner's 'Return to Home' button redirects to the home page.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Logout Test Tenant', slug='logout-test-tenant',
        )
        self.user = make_user('tester@logout.test', self.tenant, 'ADMIN')
        self.client.login(email='tester@logout.test', password='testpass123')

    def test_logout_without_next_redirects_to_login(self):
        """Default logout goes to login page."""
        response = self.client.get(reverse('core:logout'))
        self.assertRedirects(response, reverse('core:login'))

    def test_logout_with_next_redirects_to_home(self):
        """Logout with ?next=/ redirects to the home page."""
        home_url = reverse('core:home')
        response = self.client.get(reverse('core:logout') + f'?next={home_url}')
        self.assertRedirects(response, home_url)

    def test_logout_with_custom_next_redirects_correctly(self):
        """Logout with any ?next= value redirects to that URL."""
        demo_url = reverse('core:demo_hub')
        response = self.client.get(reverse('core:logout') + f'?next={demo_url}')
        self.assertRedirects(response, demo_url)


# ---------------------------------------------------------------------------
# 3. CashTransfer Form — Attendant Recipient Restriction
# ---------------------------------------------------------------------------

class CashTransferFormRecipientTests(TestCase):
    """
    Verify that a SHOP_ATTENDANT's CashTransferForm only lists
    their Shop Manager as an available recipient.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Transfer Test Tenant', slug='transfer-test-tenant',
        )
        self.shop = Location.objects.create(
            tenant=self.tenant, name='Shop B', location_type='SHOP',
        )
        other_shop = Location.objects.create(
            tenant=self.tenant, name='Shop C', location_type='SHOP',
        )

        self.attendant = make_user('attendant@transfer.test', self.tenant, 'SHOP_ATTENDANT', self.shop)
        self.manager = make_user('manager@transfer.test', self.tenant, 'SHOP_MANAGER', self.shop)
        self.other_manager = make_user('other_manager@transfer.test', self.tenant, 'SHOP_MANAGER', other_shop)
        self.accountant = make_user('accountant@transfer.test', self.tenant, 'ACCOUNTANT')

    def test_attendant_transfer_form_only_shows_own_shop_manager(self):
        """Attendant can only transfer to their shop's manager."""
        from apps.accounting.forms import CashTransferForm
        form = CashTransferForm(user=self.attendant)

        recipients = list(form.fields['to_user'].queryset)
        self.assertIn(self.manager, recipients)
        self.assertNotIn(self.other_manager, recipients)
        self.assertNotIn(self.accountant, recipients)

    def test_attendant_form_has_no_destination_field(self):
        """The destination field is not exposed to attendants in the form."""
        from apps.accounting.forms import CashTransferForm
        form = CashTransferForm(user=self.attendant)
        self.assertNotIn('destination', form.fields)

    def test_bank_transfer_does_not_appear_in_accountant_cash_on_hand(self):
        """
        A CashTransfer with destination=BANK should NOT increase the
        accountant's cash_on_hand — it exits the system.
        """
        # Give cashier some cash via a sale
        Sale.objects.create(
            tenant=self.tenant, shop=self.shop, attendant=self.attendant,
            total=Decimal('100.00'), amount_paid=Decimal('100.00'),
            status='COMPLETED', payment_method='CASH',
        )

        # Transfer directly to BANK (confirmed)
        CashTransfer.objects.create(
            tenant=self.tenant,
            amount=Decimal('100.00'),
            transfer_type='DEPOSIT',
            destination='BANK',
            status='CONFIRMED',
            from_user=self.attendant,
            from_location=self.shop,
            to_user=self.accountant,
        )

        # Accountant's cash_on_hand should NOT include bank-destined transfers
        ctx = get_context(self.accountant)
        self.assertEqual(ctx['cash_on_hand'], Decimal('0.00'))
