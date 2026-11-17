from decimal import Decimal
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from apps.inventory.models import Product, InventoryLedger
from apps.inventory.views import ProductDetailView
from apps.core.models import Tenant, Location
from apps.sales.models import Sale, SaleItem

User = get_user_model()

class ProductDetailPendingDispatchTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(
            name="Strict Test Tenant",
            slug="stricttest",
            currency="GHS",
            use_strict_sales_workflow=True
        )
        self.user = User.objects.create_user(
            email="strictmgr@test.com",
            password="password123",
            tenant=self.tenant
        )
        self.location = Location.objects.create(
            name="Main Branch",
            tenant=self.tenant,
            location_type="SHOP"
        )
        self.product = Product.objects.create(
            name="Test Widget",
            sku="WIDGET-001",
            tenant=self.tenant,
            default_selling_price=Decimal("20.00"),
            reorder_level=Decimal("10.00")
        )

        # Physical stock = 50
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location,
            quantity=Decimal("50.00")
        )

        # Sale 1: Pending dispatch, 15 items, 0 dispatched
        self.sale1 = Sale.objects.create(
            tenant=self.tenant,
            sale_number="SALE-001",
            shop=self.location,
            attendant=self.user,
            status='PENDING_DISPATCH'
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=self.sale1,
            product=self.product,
            quantity=Decimal("15.00"),
            unit_price=Decimal("20.00"),
            total=Decimal("300.00"),
            dispatched_quantity=Decimal("0.00")
        )

        # Sale 2: Partially dispatched, 10 items, 4 dispatched -> 6 pending
        self.sale2 = Sale.objects.create(
            tenant=self.tenant,
            sale_number="SALE-002",
            shop=self.location,
            attendant=self.user,
            status='PARTIALLY_DISPATCHED'
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=self.sale2,
            product=self.product,
            quantity=Decimal("10.00"),
            unit_price=Decimal("20.00"),
            total=Decimal("200.00"),
            dispatched_quantity=Decimal("4.00")
        )

    def test_strict_workflow_pending_dispatch_context_and_render(self):
        request = self.factory.get(f'/inventory/products/{self.product.pk}/')
        request.user = self.user

        view = ProductDetailView.as_view()
        response = view(request, pk=self.product.pk)
        self.assertEqual(response.status_code, 200)

        # Context assertions
        ctx = response.context_data
        self.assertTrue(ctx['has_strict_workflow'])
        self.assertEqual(ctx['total_pending_dispatch_qty'], Decimal("21.00")) # 15 + 6
        self.assertEqual(ctx['pending_dispatch_sales_count'], 2)
        self.assertEqual(ctx['net_available_stock'], Decimal("29.00")) # 50 - 21

        # Check stock by location annotation
        loc_stock = ctx['stock_by_location'][0]
        self.assertEqual(loc_stock['pending_dispatch'], Decimal("21.00"))
        self.assertEqual(loc_stock['net_available'], Decimal("29.00"))

        # Render HTML assertions
        response.render()
        html = response.content.decode('utf-8')
        self.assertIn("Pending Dispatch", html)
        self.assertIn("21.00", html)
        self.assertIn("2 sales pending", html)
        self.assertIn("Net Avail:", html)
        self.assertIn("29.00", html)

    def test_shop_manager_restricted_to_assigned_shop(self):
        from apps.core.models import Role
        sm_role, _ = Role.objects.get_or_create(
            name="SHOP_MANAGER",
            defaults={"description": "Shop Manager"}
        )
        self.user.role = sm_role
        self.user.location = self.location
        self.user.save()

        # Create second location
        other_location = Location.objects.create(
            name="Branch 2",
            tenant=self.tenant,
            location_type="SHOP"
        )
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=other_location,
            quantity=Decimal("100.00")
        )

        # Attempt to access with other location in query params
        request = self.factory.get(f'/inventory/products/{self.product.pk}/?location={other_location.pk}')
        request.user = self.user

        view = ProductDetailView.as_view()
        response = view(request, pk=self.product.pk)
        self.assertEqual(response.status_code, 200)

        ctx = response.context_data
        self.assertFalse(ctx['can_filter_locations'])
        self.assertEqual(ctx['selected_location'], self.location)
        self.assertEqual(ctx['total_stock_scope'], Decimal("50.00")) # Only self.location
        self.assertEqual(ctx['overall_total_stock'], Decimal("50.00")) # Not 150
        self.assertEqual(len(ctx['stock_by_location']), 1)
        self.assertEqual(ctx['stock_by_location'][0]['location__name'], "Main Branch")
