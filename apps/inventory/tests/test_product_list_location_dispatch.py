from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.core.models import Tenant, Location, Role
from apps.inventory.models import Product, Category, InventoryLedger
from apps.sales.models import Sale, SaleItem

User = get_user_model()


class ProductListLocationAndDispatchTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.tenant = Tenant.objects.create(
            name="Retail Corp",
            slug="retail-corp",
            currency="GHS",
            use_strict_sales_workflow=True
        )

        self.admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        self.accountant_role, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        self.auditor_role, _ = Role.objects.get_or_create(name='AUDITOR')
        self.shop_manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.attendant_role, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')

        self.shop_north = Location.objects.create(
            tenant=self.tenant,
            name="Shop North",
            location_type="SHOP"
        )
        self.shop_south = Location.objects.create(
            tenant=self.tenant,
            name="Shop South",
            location_type="SHOP"
        )

        self.admin = User.objects.create_user(
            email="admin@retail.com",
            password="password123",
            tenant=self.tenant,
            role=self.admin_role,
            is_staff=True
        )

        self.accountant = User.objects.create_user(
            email="accountant@retail.com",
            password="password123",
            tenant=self.tenant,
            role=self.accountant_role
        )

        self.attendant_north = User.objects.create_user(
            email="cashier.north@retail.com",
            password="password123",
            tenant=self.tenant,
            role=self.attendant_role,
            location=self.shop_north
        )

        self.category = Category.objects.create(tenant=self.tenant, name="Beverages")

        self.product_a = Product.objects.create(
            tenant=self.tenant,
            name="Mineral Water 500ml",
            sku="WATER-500",
            category=self.category,
            default_selling_price=Decimal("5.00"),
            reorder_level=Decimal("10.00")
        )
        self.product_b = Product.objects.create(
            tenant=self.tenant,
            name="Energy Drink 330ml",
            sku="ENERGY-330",
            category=self.category,
            default_selling_price=Decimal("12.00"),
            reorder_level=Decimal("5.00")
        )

        # Inventory at Shop North: 50 of Product A, 10 of Product B
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product_a,
            location=self.shop_north,
            quantity=Decimal("50.00")
        )
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product_b,
            location=self.shop_north,
            quantity=Decimal("10.00")
        )

        # Inventory at Shop South: 30 of Product A, 40 of Product B
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product_a,
            location=self.shop_south,
            quantity=Decimal("30.00")
        )
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product_b,
            location=self.shop_south,
            quantity=Decimal("40.00")
        )

        # Sales at Shop North:
        # Sale 1: 15 of Product A pending dispatch (0 dispatched)
        sale_n1 = Sale.objects.create(
            tenant=self.tenant,
            sale_number="SALE-N1",
            shop=self.shop_north,
            attendant=self.attendant_north,
            status='PENDING_DISPATCH'
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale_n1,
            product=self.product_a,
            quantity=Decimal("15.00"),
            unit_price=Decimal("5.00"),
            total=Decimal("75.00"),
            dispatched_quantity=Decimal("0.00")
        )

        # Sales at Shop South:
        # Sale 2: 20 of Product A pending dispatch (5 dispatched -> 15 pending)
        sale_s1 = Sale.objects.create(
            tenant=self.tenant,
            sale_number="SALE-S1",
            shop=self.shop_south,
            attendant=self.admin,
            status='PARTIALLY_DISPATCHED'
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale_s1,
            product=self.product_a,
            quantity=Decimal("20.00"),
            unit_price=Decimal("5.00"),
            total=Decimal("100.00"),
            dispatched_quantity=Decimal("5.00")
        )

    def test_company_wide_stock_for_admin_when_no_location_filtered(self):
        """When no location filter is active, admin sees total company-wide stock."""
        self.client.force_login(self.admin)
        url = reverse('inventory:product_list')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        stock_map = response.context['stock_by_product']

        # Product A: 50 (North) + 30 (South) = 80
        self.assertEqual(Decimal(str(stock_map[self.product_a.id])), Decimal("80.00"))
        # Product B: 10 (North) + 40 (South) = 50
        self.assertEqual(Decimal(str(stock_map[self.product_b.id])), Decimal("50.00"))
        # Summary total units: 80 + 50 = 130
        self.assertEqual(Decimal(str(response.context['summary_total_units'])), Decimal("130.00"))
        self.assertIsNone(response.context['selected_location'])

    def test_location_filtering_shop_north(self):
        """Filtering by Shop North shows only Shop North inventory and recalculates metrics."""
        self.client.force_login(self.admin)
        url = reverse('inventory:product_list') + f'?location={self.shop_north.pk}'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        stock_map = response.context['stock_by_product']

        # Shop North has 50 Product A, 10 Product B
        self.assertEqual(Decimal(str(stock_map[self.product_a.id])), Decimal("50.00"))
        self.assertEqual(Decimal(str(stock_map[self.product_b.id])), Decimal("10.00"))
        self.assertEqual(Decimal(str(response.context['summary_total_units'])), Decimal("60.00"))
        self.assertEqual(response.context['selected_location'], self.shop_north)

    def test_location_filtering_shop_south(self):
        """Filtering by Shop South shows only Shop South inventory."""
        self.client.force_login(self.admin)
        url = reverse('inventory:product_list') + f'?location={self.shop_south.pk}'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        stock_map = response.context['stock_by_product']

        # Shop South has 30 Product A, 40 Product B
        self.assertEqual(Decimal(str(stock_map[self.product_a.id])), Decimal("30.00"))
        self.assertEqual(Decimal(str(stock_map[self.product_b.id])), Decimal("40.00"))
        self.assertEqual(Decimal(str(response.context['summary_total_units'])), Decimal("70.00"))

    def test_dispatch_toggle_displays_pending_and_net_stock(self):
        """Admin toggling show_dispatch=1 receives physical, pending dispatch, and net available data."""
        self.client.force_login(self.admin)
        url = reverse('inventory:product_list') + '?show_dispatch=1'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_dispatch'])
        self.assertTrue(response.context['can_view_dispatch'])

        pending_map = response.context['pending_by_product']
        net_map = response.context['net_available_by_product']

        # Company-wide:
        # Product A: Physical=80, Pending Dispatch = 15 (North) + 15 (South) = 30, Net = 50
        self.assertEqual(Decimal(str(pending_map[self.product_a.id])), Decimal("30.00"))
        self.assertEqual(Decimal(str(net_map[self.product_a.id])), Decimal("50.00"))

        # Product B has 0 pending dispatch, net = 50
        self.assertEqual(Decimal(str(pending_map.get(self.product_b.id, Decimal('0.00')))), Decimal("0.00"))
        self.assertEqual(Decimal(str(net_map[self.product_b.id])), Decimal("50.00"))

        # Summary cards in dispatch mode
        self.assertEqual(Decimal(str(response.context['summary_total_pending_units'])), Decimal("30.00"))
        self.assertEqual(Decimal(str(response.context['summary_total_net_units'])), Decimal("100.00"))

    def test_dispatch_toggle_with_specific_location(self):
        """Filtering location and toggling show_dispatch scopes pending dispatch to that location."""
        self.client.force_login(self.accountant)
        url = reverse('inventory:product_list') + f'?location={self.shop_north.pk}&show_dispatch=1'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        pending_map = response.context['pending_by_product']
        net_map = response.context['net_available_by_product']

        # Shop North:
        # Product A: Physical = 50, Pending = 15, Net = 35
        self.assertEqual(Decimal(str(pending_map[self.product_a.id])), Decimal("15.00"))
        self.assertEqual(Decimal(str(net_map[self.product_a.id])), Decimal("35.00"))

    def test_stock_status_filter_has_pending_dispatch(self):
        """Filter by stock_status=has_pending_dispatch returns only products with pending dispatch."""
        self.client.force_login(self.admin)
        url = reverse('inventory:product_list') + '?stock_status=has_pending_dispatch&show_dispatch=1'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        products = list(response.context['products'])

        # Only Product A has pending dispatch
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].id, self.product_a.id)

    def test_non_hq_attendant_cannot_view_dispatch_toggle(self):
        """Cashiers/attendants cannot view or activate dispatch audit mode."""
        self.client.force_login(self.attendant_north)
        url = reverse('inventory:product_list') + '?show_dispatch=1'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_dispatch'])
        self.assertFalse(response.context['show_dispatch'])
        self.assertEqual(response.context['selected_location'], self.shop_north)

    def test_excel_export_respects_location_and_dispatch(self):
        """ExportView generates Excel workbook with location and dispatch columns."""
        self.client.force_login(self.admin)
        url = reverse('inventory:product_export') + f'?location={self.shop_north.pk}&show_dispatch=1'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('Shop_North', response['Content-Disposition'])
