"""
Tests for the partial dispatch feature in the strict sales workflow.

Verifies:
- Full dispatch: all items at once marks the sale COMPLETED and deducts inventory.
- Partial dispatch: dispatching a subset of items keeps the sale PENDING_DISPATCH,
  deducts only the dispatched quantities from inventory.
- Second dispatch: completing the remaining items finalises the sale to COMPLETED.
- Over-dispatch guard: cannot dispatch more than remaining quantity.
- Role guard: only SHOP_MANAGER and ADMIN can dispatch.
- Idempotency: a fully dispatched sale cannot be dispatched again.
"""
import json
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Sale, SaleItem
from apps.inventory.models import Product, Category, Batch, InventoryLedger


class PartialDispatchTests(TestCase):
    """
    End-to-end tests for the partial dispatch API endpoint.
    All tests use the strict sales workflow (PENDING_DISPATCH status).
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Dispatch Tenant",
            use_strict_sales_workflow=True
        )
        self.manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.cashier_role, _ = Role.objects.get_or_create(name='SHOP_CASHIER')
        self.admin_role, _ = Role.objects.get_or_create(name='ADMIN')

        self.shop = Location.objects.create(
            name="Main Shop", tenant=self.tenant, location_type='SHOP'
        )

        self.manager = User.objects.create_user(
            email="manager@test.com", password="pass123",
            tenant=self.tenant, role=self.manager_role, location=self.shop
        )
        self.cashier = User.objects.create_user(
            email="cashier@test.com", password="pass123",
            tenant=self.tenant, role=self.cashier_role, location=self.shop
        )
        self.admin = User.objects.create_user(
            email="admin@test.com", password="pass123",
            tenant=self.tenant, role=self.admin_role
        )

        # Products and batches
        self.category = Category.objects.create(name="Pharma", tenant=self.tenant)

        self.product_a = Product.objects.create(
            name="Paracetamol", sku="PCM-001",
            tenant=self.tenant, category=self.category,
            default_selling_price=Decimal('10.00')
        )
        self.product_b = Product.objects.create(
            name="Ibuprofen", sku="IBU-001",
            tenant=self.tenant, category=self.category,
            default_selling_price=Decimal('15.00')
        )

        # Stock batches at the shop
        self.batch_a = Batch.objects.create(
            tenant=self.tenant, product=self.product_a, location=self.shop,
            batch_number="BA-001", initial_quantity=Decimal('50'), current_quantity=Decimal('50'),
            unit_cost=Decimal('6.00'), status='AVAILABLE'
        )
        self.batch_b = Batch.objects.create(
            tenant=self.tenant, product=self.product_b, location=self.shop,
            batch_number="BB-001", initial_quantity=Decimal('30'), current_quantity=Decimal('30'),
            unit_cost=Decimal('9.00'), status='AVAILABLE'
        )

        # A PENDING_DISPATCH sale with 2 items (typical strict workflow scenario)
        self.sale = Sale.objects.create(
            tenant=self.tenant, shop=self.shop,
            sale_number="SL-DISP-001",
            status='PENDING_DISPATCH',
            payment_method='CASH',
            amount_paid=Decimal('200.00'),
            total=Decimal('200.00'),
            attendant=self.cashier,
        )
        # 10 x Paracetamol, 5 x Ibuprofen
        self.item_a = SaleItem.objects.create(
            sale=self.sale, tenant=self.tenant,
            product=self.product_a, batch=self.batch_a,
            quantity=Decimal('10'), unit_price=Decimal('10.00'),
            dispatched_quantity=Decimal('0')
        )
        self.item_b = SaleItem.objects.create(
            sale=self.sale, tenant=self.tenant,
            product=self.product_b, batch=self.batch_b,
            quantity=Decimal('5'), unit_price=Decimal('15.00'),
            dispatched_quantity=Decimal('0')
        )

        self.dispatch_url = reverse('sales:api_dispatch_sale', args=[self.sale.pk])

    def _dispatch(self, items=None, user=None):
        """Helper: POST to the dispatch endpoint as the given user."""
        if user is None:
            user = self.manager
        self.client.force_login(user)
        body = json.dumps({'items': items}) if items is not None else json.dumps({})
        return self.client.post(
            self.dispatch_url,
            data=body,
            content_type='application/json'
        )

    # ---------------------------------------------------------------- role guard

    def test_cashier_cannot_dispatch(self):
        """SHOP_CASHIER must receive a 403 when attempting to dispatch."""
        response = self._dispatch(user=self.cashier)
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertIn('error', data)

    def test_manager_can_dispatch(self):
        """SHOP_MANAGER should receive a 200 response."""
        response = self._dispatch(
            items=[{'sale_item_id': self.item_a.id, 'qty': 5}]
        )
        if response.status_code != 200:
            print("ERROR RESPONSE:", response.json())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

    def test_admin_can_dispatch(self):
        """ADMIN should also be able to dispatch."""
        response = self._dispatch(
            items=[{'sale_item_id': self.item_a.id, 'qty': 3}],
            user=self.admin
        )
        self.assertEqual(response.status_code, 200)

    # --------------------------------------------------------------- partial dispatch

    def test_partial_dispatch_keeps_sale_pending(self):
        """Dispatching some items (not all) must keep the sale PENDING_DISPATCH."""
        response = self._dispatch(
            items=[{'sale_item_id': self.item_a.id, 'qty': 5}]  # only 5 of 10
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['fully_dispatched'])

        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, 'PENDING_DISPATCH',
                         "Sale should stay PENDING_DISPATCH after partial dispatch")
        self.assertFalse(self.sale.is_dispatched)

    def test_partial_dispatch_updates_dispatched_quantity(self):
        """Partial dispatch must correctly update dispatched_quantity on the SaleItem."""
        self._dispatch(items=[{'sale_item_id': self.item_a.id, 'qty': 4}])
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.dispatched_quantity, Decimal('4'))
        self.assertEqual(self.item_a.remaining_quantity, Decimal('6'))

    def test_partial_dispatch_creates_inventory_ledger_entry(self):
        """Each partial dispatch must generate an InventoryLedger SALE entry."""
        ledger_count_before = InventoryLedger.objects.filter(
            tenant=self.tenant, product=self.product_a, transaction_type='SALE'
        ).count()

        self._dispatch(items=[{'sale_item_id': self.item_a.id, 'qty': 3}])

        ledger_count_after = InventoryLedger.objects.filter(
            tenant=self.tenant, product=self.product_a, transaction_type='SALE'
        ).count()
        self.assertEqual(ledger_count_after, ledger_count_before + 1,
                         "A new InventoryLedger SALE entry must be created for each partial dispatch")

        entry = InventoryLedger.objects.filter(
            tenant=self.tenant, product=self.product_a, transaction_type='SALE'
        ).latest('id')
        self.assertEqual(entry.quantity, Decimal('-3'),
                         "Ledger entry quantity must be -3 (deduction)")

    def test_partial_dispatch_two_items_simultaneously(self):
        """Can dispatch partial quantities for multiple items in one call."""
        response = self._dispatch(items=[
            {'sale_item_id': self.item_a.id, 'qty': 3},
            {'sale_item_id': self.item_b.id, 'qty': 2},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['fully_dispatched'])

        self.item_a.refresh_from_db()
        self.item_b.refresh_from_db()
        self.assertEqual(self.item_a.dispatched_quantity, Decimal('3'))
        self.assertEqual(self.item_b.dispatched_quantity, Decimal('2'))

    # ---------------------------------------------------------------- full dispatch

    def test_full_dispatch_in_one_call_completes_sale(self):
        """Dispatching all items at once must mark the sale COMPLETED and is_dispatched=True."""
        response = self._dispatch(items=[
            {'sale_item_id': self.item_a.id, 'qty': 10},
            {'sale_item_id': self.item_b.id, 'qty': 5},
        ])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['fully_dispatched'])
        self.assertIsNotNone(data['waybill_url'])

        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, 'COMPLETED')
        self.assertTrue(self.sale.is_dispatched)

    def test_no_items_payload_dispatches_all_remaining(self):
        """Sending an empty items list dispatches ALL remaining quantities."""
        response = self._dispatch(items=[])   # empty → dispatch everything
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['fully_dispatched'])

        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, 'COMPLETED')
        self.assertTrue(self.sale.is_dispatched)

    def test_sequential_partial_then_full_dispatch_completes_sale(self):
        """Two sequential calls: first partial, then the rest — sale should complete on second."""
        # Round 1: dispatch 7 of 10 item_a and all 5 item_b
        self._dispatch(items=[
            {'sale_item_id': self.item_a.id, 'qty': 7},
            {'sale_item_id': self.item_b.id, 'qty': 5},
        ])
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, 'PENDING_DISPATCH',
                         "After first partial dispatch, sale should still be PENDING_DISPATCH")

        # Round 2: dispatch remaining 3 of item_a
        response = self._dispatch(items=[
            {'sale_item_id': self.item_a.id, 'qty': 3},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['fully_dispatched'])

        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, 'COMPLETED',
                         "After all items dispatched, sale must be COMPLETED")
        self.assertTrue(self.sale.is_dispatched)

        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.dispatched_quantity, Decimal('10'),
                         "item_a should show total dispatched_quantity of 10")

    # ----------------------------------------------------------- over-dispatch guard

    def test_over_dispatch_returns_400(self):
        """Attempting to dispatch more than the remaining quantity must return 400."""
        response = self._dispatch(items=[
            {'sale_item_id': self.item_a.id, 'qty': 999}  # way more than 10
        ])
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_over_dispatch_is_atomic_no_partial_save(self):
        """If one item's qty is invalid, the entire dispatch call must roll back."""
        response = self._dispatch(items=[
            {'sale_item_id': self.item_a.id, 'qty': 5},    # valid
            {'sale_item_id': self.item_b.id, 'qty': 999},  # invalid
        ])
        self.assertEqual(response.status_code, 400)

        # item_a must NOT have been updated (atomic rollback)
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.dispatched_quantity, Decimal('0'),
                         "item_a dispatched_quantity must not change if the call fails atomically")

    # ------------------------------------------------------------- idempotency guard

    def test_already_dispatched_sale_returns_400(self):
        """A fully dispatched sale must return 400 on a second dispatch attempt."""
        # Fully dispatch first
        self._dispatch(items=[])

        # Try again
        response = self._dispatch(items=[])
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    # -------------------------------------------------------------- property tests

    def test_saleitem_remaining_quantity_property(self):
        """remaining_quantity must equal quantity minus dispatched_quantity."""
        self.item_a.dispatched_quantity = Decimal('3')
        self.item_a.save()
        self.assertEqual(self.item_a.remaining_quantity, Decimal('7'))

    def test_saleitem_is_fully_dispatched_property(self):
        """is_fully_dispatched must be True only when dispatched_quantity == quantity."""
        self.item_a.dispatched_quantity = Decimal('10')
        self.item_a.save()
        self.assertTrue(self.item_a.is_fully_dispatched)

    def test_saleitem_not_fully_dispatched(self):
        """is_fully_dispatched must be False when dispatched_quantity < quantity."""
        self.item_a.dispatched_quantity = Decimal('9')
        self.item_a.save()
        self.assertFalse(self.item_a.is_fully_dispatched)

    def test_sale_all_items_dispatched_property(self):
        """all_items_dispatched property on Sale must reflect all items status."""
        # Initially nothing dispatched
        self.assertFalse(self.sale.all_items_dispatched)

        # Dispatch all
        self.item_a.dispatched_quantity = Decimal('10')
        self.item_a.save()
        self.item_b.dispatched_quantity = Decimal('5')
        self.item_b.save()

        self.assertTrue(self.sale.all_items_dispatched)
