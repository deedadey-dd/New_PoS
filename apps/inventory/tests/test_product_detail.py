from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from apps.core.models import Tenant, Role, User, Location
from apps.inventory.models import Product, Batch, Category, InventoryLedger


class ProductDetailFilteringTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Product Detail Tenant", shops_can_see_other_stock=False)
        
        self.admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        self.shop_manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.cashier_role, _ = Role.objects.get_or_create(name='SHOP_CASHIER')
        self.stores_manager_role, _ = Role.objects.get_or_create(name='STORES_MANAGER')

        # Locations
        self.loc_shop_1 = Location.objects.create(
            name="Downtown Shop", tenant=self.tenant, location_type="SHOP"
        )
        self.loc_shop_2 = Location.objects.create(
            name="Uptown Shop", tenant=self.tenant, location_type="SHOP"
        )
        self.loc_store = Location.objects.create(
            name="Central Warehouse", tenant=self.tenant, location_type="STORES"
        )

        # Users
        self.admin_user = User.objects.create_user(
            email="admin@test.com", password="password123",
            tenant=self.tenant, role=self.admin_role
        )
        self.shop_manager = User.objects.create_user(
            email="manager@test.com", password="password123",
            tenant=self.tenant, role=self.shop_manager_role, location=self.loc_shop_1
        )
        self.cashier = User.objects.create_user(
            email="cashier@test.com", password="password123",
            tenant=self.tenant, role=self.cashier_role, location=self.loc_shop_1
        )

        # Product & Category
        self.category = Category.objects.create(name="Groceries", tenant=self.tenant)
        self.product = Product.objects.create(
            tenant=self.tenant,
            name="Test Juice",
            sku="JUC-001",
            category=self.category,
            default_selling_price=Decimal('20.00'),
            reorder_level=Decimal('15.00')
        )

        # Batches
        self.batch_shop1 = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.loc_shop_1,
            batch_number="BATCH-SH1-01",
            initial_quantity=Decimal('50.00'),
            current_quantity=Decimal('40.00'),
            unit_cost=Decimal('12.00'),
            status='AVAILABLE'
        )
        self.batch_shop2 = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.loc_shop_2,
            batch_number="BATCH-SH2-01",
            initial_quantity=Decimal('30.00'),
            current_quantity=Decimal('25.00'),
            unit_cost=Decimal('11.00'),
            status='AVAILABLE'
        )

        # Inventory Ledgers (Movements)
        now = timezone.now()
        # Today inflow shop 1
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.loc_shop_1,
            batch=self.batch_shop1,
            transaction_type='RECEIVING',
            quantity=Decimal('50.00'),
            unit_cost=Decimal('12.00'),
            created_at=now
        )
        # Today outflow shop 1 (Sale)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.loc_shop_1,
            batch=self.batch_shop1,
            transaction_type='SALE',
            quantity=Decimal('-10.00'),
            unit_cost=Decimal('12.00'),
            created_at=now
        )
        # Older movement shop 2 (40 days ago)
        old_date = now - timedelta(days=40)
        old_ledger = InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.loc_shop_2,
            batch=self.batch_shop2,
            transaction_type='RECEIVING',
            quantity=Decimal('25.00'),
            unit_cost=Decimal('11.00')
        )
        InventoryLedger.objects.filter(pk=old_ledger.pk).update(created_at=old_date)

        self.url = reverse('inventory:product_detail', kwargs={'pk': self.product.pk})

    def test_admin_product_detail_view_permissions_and_all_locations(self):
        """Admin can view the product detail and has access to all active locations."""
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_filter_locations'])
        self.assertContains(response, "Downtown Shop")
        self.assertContains(response, "Uptown Shop")
        self.assertContains(response, "Central Warehouse")
        self.assertEqual(len(response.context['batches']), 2)

    def test_shop_manager_cannot_filter_other_shop_locations(self):
        """Shop manager is restricted to their assigned shop and cannot filter other locations."""
        self.client.force_login(self.shop_manager)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_filter_locations'])
        self.assertEqual(response.context['selected_location'], self.loc_shop_1)

    def test_filter_by_location(self):
        """Filtering by a specific location returns only batches and movements for that location."""
        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {'location': self.loc_shop_1.pk})
        self.assertEqual(response.status_code, 200)
        
        batches = response.context['batches']
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].batch_number, "BATCH-SH1-01")
        self.assertEqual(response.context['total_batch_qty'], Decimal('80.00'))
        self.assertEqual(response.context['total_stock_scope'], Decimal('40.00'))

    def test_filter_by_date_range(self):
        """Filtering by date range limits inventory activity entries correctly."""
        self.client.force_login(self.admin_user)
        
        # Today's activity
        response = self.client.get(self.url, {'range': 'today'})
        self.assertEqual(response.status_code, 200)
        entries = response.context['recent_entries']
        # Today should only have the 2 shop 1 transactions, not the 40-day old shop 2 transaction
        self.assertEqual(len(entries), 2)
        self.assertEqual(response.context['total_inflow'], Decimal('50.00'))
        self.assertEqual(response.context['total_outflow'], Decimal('10.00'))
        self.assertEqual(response.context['net_movement'], Decimal('40.00'))
        self.assertEqual(response.context['period_sales'], Decimal('10.00'))

    def test_cashier_location_scoping(self):
        """Cashier without cross-shop permission cannot filter other locations."""
        self.client.force_login(self.cashier)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_filter_locations'])
        # Cashier's batches should only be for their assigned location (Downtown Shop)
        batches = response.context['batches']
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].location, self.loc_shop_1)

