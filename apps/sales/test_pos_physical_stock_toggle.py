from decimal import Decimal
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from apps.core.models import Tenant, Location
from apps.inventory.models import Product, InventoryLedger, ShopPrice
from apps.sales.models import Sale, SaleItem, Shift, ShopSettings
from apps.sales.views import POSView

User = get_user_model()

class POSPhysicalStockToggleTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(
            name="Strict POS Tenant",
            slug="strict-pos",
            currency="GHS",
            use_strict_sales_workflow=True
        )
        self.shop = Location.objects.create(
            name="Main Shop",
            tenant=self.tenant,
            location_type="SHOP"
        )
        self.user = User.objects.create_user(
            email="attendant@test.com",
            password="password123",
            tenant=self.tenant,
            location=self.shop
        )
        self.shop_settings = ShopSettings.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            hide_zero_stock_in_pos=True,
            show_items_with_physical_stock_in_pos=False
        )
        self.product = Product.objects.create(
            name="Test Pen",
            sku="PEN-100",
            tenant=self.tenant,
            default_selling_price=Decimal("5.00"),
            reorder_level=Decimal("5.00")
        )
        ShopPrice.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
            selling_price=Decimal("5.00"),
            is_active=True
        )
        self.shift = Shift.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.user,
            status='OPEN',
            opening_cash=Decimal("50.00")
        )

        # 50 physical items in inventory
        InventoryLedger.objects.create(
            tenant=self.tenant,
            location=self.shop,
            product=self.product,
            quantity=Decimal("50.00")
        )

        # 60 items pending dispatch on an active sale
        self.sale = Sale.objects.create(
            tenant=self.tenant,
            sale_number="SALE-STRICT-01",
            shop=self.shop,
            attendant=self.user,
            status='PENDING_DISPATCH'
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=self.sale,
            product=self.product,
            quantity=Decimal("60.00"),
            unit_price=Decimal("5.00"),
            total=Decimal("300.00"),
            dispatched_quantity=Decimal("0.00")
        )

    def test_item_hidden_when_physical_stock_toggle_off(self):
        # hide_zero_stock_in_pos is True, secondary toggle is False
        # Net available is 50 - 60 = 0 (or negative)
        # Expected: Product is hidden from POS
        self.shop_settings.hide_zero_stock_in_pos = True
        self.shop_settings.show_items_with_physical_stock_in_pos = False
        self.shop_settings.save()

        self.client.force_login(self.user)
        response = self.client.get('/sales/pos/')
        self.assertEqual(response.status_code, 200)

        import json
        products = json.loads(response.context['products'])
        p_ids = [p['id'] for p in products]
        self.assertNotIn(self.product.pk, p_ids)

    def test_item_shown_when_physical_stock_toggle_on(self):
        # secondary toggle is True
        # As long as there is physical stock in inventory (50), product shows in POS
        self.shop_settings.hide_zero_stock_in_pos = True
        self.shop_settings.show_items_with_physical_stock_in_pos = True
        self.shop_settings.save()

        self.client.force_login(self.user)
        response = self.client.get('/sales/pos/')
        self.assertEqual(response.status_code, 200)

        import json
        products = json.loads(response.context['products'])
        matching = [p for p in products if p['id'] == self.product.pk]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]['quantity'], 50.0)

    def test_item_hidden_if_physical_stock_is_zero_even_with_toggle_on(self):
        # If inventory ledger stock is actually 0, it should be hidden when hide_zero_stock_in_pos=True
        InventoryLedger.objects.filter(product=self.product, location=self.shop).delete()

        self.shop_settings.hide_zero_stock_in_pos = True
        self.shop_settings.show_items_with_physical_stock_in_pos = True
        self.shop_settings.save()

        self.client.force_login(self.user)
        response = self.client.get('/sales/pos/')
        self.assertEqual(response.status_code, 200)

        import json
        products = json.loads(response.context['products'])
        p_ids = [p['id'] for p in products]
        self.assertNotIn(self.product.pk, p_ids)

    def test_invoice_creation_allowed_when_physical_minus_undispatched_is_negative(self):
        # Physical stock = 50, undispatched = 60 (net = -10, negative!)
        # With show_items_with_physical_stock_in_pos = True, attendants CAN create invoices up to 50
        self.shop_settings.show_items_with_physical_stock_in_pos = True
        self.shop_settings.save()

        self.client.force_login(self.user)
        import json
        payload = {
            'items': [{'product_id': self.product.pk, 'quantity': '10.00', 'unit_price': '5.00'}],
            'payment_method': 'PENDING_INVOICE',
            'customer_name': 'Test Buyer',
            'customer_phone': '0555123456',
        }
        resp = self.client.post('/sales/api/checkout/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get('success'))
        self.assertTrue('sale_number' in data)

    def test_invoice_creation_blocked_when_exceeds_physical_stock(self):
        # Physical stock = 50. Attendant tries to invoice 55.
        self.shop_settings.show_items_with_physical_stock_in_pos = True
        self.shop_settings.save()

        self.client.force_login(self.user)
        import json
        payload = {
            'items': [{'product_id': self.product.pk, 'quantity': '55.00', 'unit_price': '5.00'}],
            'payment_method': 'PENDING_INVOICE',
            'customer_name': 'Test Buyer',
            'customer_phone': '0555123456',
        }
        resp = self.client.post('/sales/api/checkout/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn('physically in stock', data.get('error', ''))

    def test_admin_can_update_specific_shop_settings_via_query_param(self):
        """Admin without assigned location can configure a specific shop using ?shop=<id>."""
        from apps.core.models import Role
        admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        admin_user = User.objects.create_user(
            email="admin_strict@test.com",
            password="password123",
            tenant=self.tenant,
            role=admin_role,
            is_staff=True,
            location=None
        )

        shop2 = Location.objects.create(
            name="Branch Shop",
            tenant=self.tenant,
            location_type="SHOP"
        )
        shop2_settings = ShopSettings.objects.create(
            tenant=self.tenant,
            shop=shop2,
            show_items_with_physical_stock_in_pos=False
        )

        self.client.force_login(admin_user)

        # GET with ?shop=shop2.pk loads shop2
        resp_get = self.client.get(f'/sales/shop-settings/?shop={shop2.pk}')
        self.assertEqual(resp_get.status_code, 200)
        self.assertEqual(resp_get.context['current_shop'], shop2)

        # POST with shop=shop2.pk updates shop2
        post_data = {
            'shop': str(shop2.pk),
            'receipt_printer_type': 'THERMAL_80MM',
            'receipt_font_family': 'CONSOLAS',
            'receipt_font_weight': 'NORMAL',
            'receipt_font_size': 'NORMAL',
            'show_items_with_physical_stock_in_pos': 'on',
            'auto_print_receipts': 'on',
        }
        resp_post = self.client.post(f'/sales/shop-settings/?shop={shop2.pk}', data=post_data)
        self.assertEqual(resp_post.status_code, 302)

        shop2_settings.refresh_from_db()
        self.assertTrue(shop2_settings.show_items_with_physical_stock_in_pos)

        # shop 1 settings remain unchanged
        self.shop_settings.refresh_from_db()
        self.assertFalse(self.shop_settings.show_items_with_physical_stock_in_pos)

    def test_admin_apply_settings_to_all_shops(self):
        """When apply_to_all_shops is checked, settings are synced across all shops of the tenant."""
        from apps.core.models import Role
        admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        admin_user = User.objects.create_user(
            email="admin_sync@test.com",
            password="password123",
            tenant=self.tenant,
            role=admin_role,
            is_staff=True,
            location=None
        )

        shop2 = Location.objects.create(
            name="Branch Shop 2",
            tenant=self.tenant,
            location_type="SHOP"
        )
        shop2_settings = ShopSettings.objects.create(
            tenant=self.tenant,
            shop=shop2,
            show_items_with_physical_stock_in_pos=False
        )

        self.client.force_login(admin_user)

        post_data = {
            'shop': str(self.shop.pk),
            'receipt_printer_type': 'THERMAL_80MM',
            'receipt_font_family': 'CONSOLAS',
            'receipt_font_weight': 'NORMAL',
            'receipt_font_size': 'NORMAL',
            'show_items_with_physical_stock_in_pos': 'on',
            'auto_print_receipts': 'on',
            'apply_to_all_shops': '1',
        }
        resp = self.client.post('/sales/shop-settings/', data=post_data)
        self.assertEqual(resp.status_code, 302)

        self.shop_settings.refresh_from_db()
        shop2_settings.refresh_from_db()
        self.assertTrue(self.shop_settings.show_items_with_physical_stock_in_pos)
        self.assertTrue(shop2_settings.show_items_with_physical_stock_in_pos)

    def test_admin_can_access_pos_and_switch_shops(self):
        """Admin without assigned shop can access POS and switch via ?shop=<id>."""
        from apps.core.models import Role
        admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        admin_user = User.objects.create_user(
            email="admin_pos@test.com",
            password="password123",
            tenant=self.tenant,
            role=admin_role,
            is_staff=True,
            location=None
        )

        shop2 = Location.objects.create(
            name="Branch Shop 3",
            tenant=self.tenant,
            location_type="SHOP"
        )

        self.client.force_login(admin_user)

        # Access POS with ?shop=shop2.pk
        resp = self.client.get(f'/sales/pos/?shop={shop2.pk}')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['shop'], shop2)
        self.assertTrue(resp.context['is_hq_or_admin'])
