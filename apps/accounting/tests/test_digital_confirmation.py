"""
Tests for the digital payment confirmation queue.
Ensures PENDING_DISPATCH sales (strict workflow - payment received, goods not yet dispatched)
appear correctly in the confirmation view for MOMO and E-Cash payments.
"""
from django.test import TestCase
from django.urls import reverse
from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Sale
from apps.customers.models import Customer, CustomerTransaction


class DigitalConfirmationPendingDispatchTests(TestCase):
    """
    Tests that MOMO and E-Cash sales in PENDING_DISPATCH status appear in the
    digital confirmation queue. In strict workflow, payment is collected before
    goods are dispatched, so these sales must be immediately confirmable.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Strict Workflow Tenant",
            use_strict_sales_workflow=True
        )
        self.accountant_role, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        self.manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.attendant_role, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')

        self.shop = Location.objects.create(
            name="Test Shop", tenant=self.tenant, location_type='SHOP'
        )

        self.accountant = User.objects.create_user(
            email="accountant@test.com", password="password123",
            tenant=self.tenant, role=self.accountant_role
        )
        self.manager = User.objects.create_user(
            email="manager@test.com", password="password123",
            tenant=self.tenant, role=self.manager_role, location=self.shop
        )
        self.attendant = User.objects.create_user(
            email="attendant@test.com", password="password123",
            tenant=self.tenant, role=self.attendant_role, location=self.shop
        )

        # MOMO sale: paid, pending dispatch (strict workflow)
        self.momo_pending_dispatch = Sale.objects.create(
            tenant=self.tenant, shop=self.shop,
            sale_number="SL-MOMO-01",
            amount_paid=150.00, total=150.00,
            status='PENDING_DISPATCH',
            payment_method='MOMO',
            is_accountant_confirmed=False,
            attendant=self.attendant,
        )

        # MOMO sale: fully completed and dispatched
        self.momo_completed = Sale.objects.create(
            tenant=self.tenant, shop=self.shop,
            sale_number="SL-MOMO-02",
            amount_paid=200.00, total=200.00,
            status='COMPLETED',
            payment_method='MOMO',
            is_accountant_confirmed=False,
            attendant=self.attendant,
        )

        # MOMO sale: voided — must NOT appear
        self.momo_voided = Sale.objects.create(
            tenant=self.tenant, shop=self.shop,
            sale_number="SL-MOMO-03",
            amount_paid=0.00, total=100.00,
            status='VOIDED',
            payment_method='MOMO',
            is_accountant_confirmed=False,
            attendant=self.attendant,
        )

        # MOMO sale: already confirmed — must NOT appear
        self.momo_already_confirmed = Sale.objects.create(
            tenant=self.tenant, shop=self.shop,
            sale_number="SL-MOMO-04",
            amount_paid=80.00, total=80.00,
            status='PENDING_DISPATCH',
            payment_method='MOMO',
            is_accountant_confirmed=True,
            attendant=self.attendant,
        )

        # E-Cash sale: paid, pending dispatch
        self.ecash_pending_dispatch = Sale.objects.create(
            tenant=self.tenant, shop=self.shop,
            sale_number="SL-ECASH-01",
            amount_paid=300.00, total=300.00,
            status='PENDING_DISPATCH',
            payment_method='ECASH',
            is_accountant_confirmed=False,
            attendant=self.attendant,
        )

        # E-Cash sale: fully completed
        self.ecash_completed = Sale.objects.create(
            tenant=self.tenant, shop=self.shop,
            sale_number="SL-ECASH-02",
            amount_paid=250.00, total=250.00,
            status='COMPLETED',
            payment_method='ECASH',
            is_accountant_confirmed=False,
            attendant=self.attendant,
        )

        # E-Cash sale: voided — must NOT appear
        self.ecash_voided = Sale.objects.create(
            tenant=self.tenant, shop=self.shop,
            sale_number="SL-ECASH-03",
            amount_paid=0.00, total=120.00,
            status='VOIDED',
            payment_method='ECASH',
            is_accountant_confirmed=False,
            attendant=self.attendant,
        )

    def _get_confirmation_page(self):
        self.client.force_login(self.accountant)
        return self.client.get(reverse('accounting:digital_confirmations'))

    def _get_sale_ids_in_response(self, response):
        return {
            tx['id']
            for tx in response.context['page_obj'].object_list
            if tx.get('tx_type') == 'sale'
        }

    # ------------------------------------------------------------------ MOMO

    def test_pending_dispatch_momo_sale_appears_in_confirmation_queue(self):
        """A MOMO sale in PENDING_DISPATCH must appear — payment is collected."""
        response = self._get_confirmation_page()
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            self.momo_pending_dispatch.id,
            self._get_sale_ids_in_response(response),
            "PENDING_DISPATCH MOMO sale should appear in digital confirmation queue"
        )

    def test_completed_momo_sale_appears_in_confirmation_queue(self):
        """A fully COMPLETED MOMO sale must still appear until confirmed."""
        response = self._get_confirmation_page()
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            self.momo_completed.id,
            self._get_sale_ids_in_response(response),
            "COMPLETED MOMO sale should appear in digital confirmation queue"
        )

    def test_voided_momo_sale_does_not_appear(self):
        """A VOIDED MOMO sale must never appear — no money was collected."""
        response = self._get_confirmation_page()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            self.momo_voided.id,
            self._get_sale_ids_in_response(response),
            "VOIDED MOMO sale must NOT appear in digital confirmation queue"
        )

    def test_already_confirmed_momo_sale_does_not_appear(self):
        """A MOMO sale already confirmed by the accountant must not appear again."""
        response = self._get_confirmation_page()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            self.momo_already_confirmed.id,
            self._get_sale_ids_in_response(response),
            "Already-confirmed MOMO sale must NOT appear in the queue"
        )

    # ----------------------------------------------------------------- ECASH

    def test_pending_dispatch_ecash_sale_appears_in_confirmation_queue(self):
        """An E-Cash sale in PENDING_DISPATCH must appear — Paystack already confirmed payment."""
        response = self._get_confirmation_page()
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            self.ecash_pending_dispatch.id,
            self._get_sale_ids_in_response(response),
            "PENDING_DISPATCH E-Cash sale should appear in digital confirmation queue"
        )

    def test_completed_ecash_sale_appears_in_confirmation_queue(self):
        """A fully COMPLETED E-Cash sale must still appear until confirmed."""
        response = self._get_confirmation_page()
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            self.ecash_completed.id,
            self._get_sale_ids_in_response(response),
            "COMPLETED E-Cash sale should appear in digital confirmation queue"
        )

    def test_voided_ecash_sale_does_not_appear(self):
        """A VOIDED E-Cash sale must never appear."""
        response = self._get_confirmation_page()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            self.ecash_voided.id,
            self._get_sale_ids_in_response(response),
            "VOIDED E-Cash sale must NOT appear in digital confirmation queue"
        )

    # ---------------------------------------------------------- Filter tests

    def test_payment_method_filter_momo_only(self):
        """Filtering by MOMO should exclude E-Cash sales from the queue."""
        self.client.force_login(self.accountant)
        response = self.client.get(
            reverse('accounting:digital_confirmations'), {'payment_method': 'MOMO'}
        )
        self.assertEqual(response.status_code, 200)
        sale_ids = self._get_sale_ids_in_response(response)
        self.assertIn(self.momo_pending_dispatch.id, sale_ids)
        self.assertIn(self.momo_completed.id, sale_ids)
        self.assertNotIn(self.ecash_pending_dispatch.id, sale_ids)
        self.assertNotIn(self.ecash_completed.id, sale_ids)

    def test_payment_method_filter_ecash_only(self):
        """Filtering by ECASH should exclude MOMO sales from the queue."""
        self.client.force_login(self.accountant)
        response = self.client.get(
            reverse('accounting:digital_confirmations'), {'payment_method': 'ECASH'}
        )
        self.assertEqual(response.status_code, 200)
        sale_ids = self._get_sale_ids_in_response(response)
        self.assertIn(self.ecash_pending_dispatch.id, sale_ids)
        self.assertIn(self.ecash_completed.id, sale_ids)
        self.assertNotIn(self.momo_pending_dispatch.id, sale_ids)
        self.assertNotIn(self.momo_completed.id, sale_ids)

    def test_all_paid_sales_in_queue(self):
        """
        Total unconfirmed sale IDs in queue must include all 4 paid sales
        (2 MOMO + 2 ECASH — regardless of PENDING_DISPATCH vs COMPLETED).
        """
        response = self._get_confirmation_page()
        self.assertEqual(response.status_code, 200)
        sale_ids = self._get_sale_ids_in_response(response)
        expected_ids = {
            self.momo_pending_dispatch.id,
            self.momo_completed.id,
            self.ecash_pending_dispatch.id,
            self.ecash_completed.id,
        }
        self.assertTrue(
            expected_ids.issubset(sale_ids),
            f"Expected these sale IDs in queue: {expected_ids}. Got: {sale_ids}"
        )
