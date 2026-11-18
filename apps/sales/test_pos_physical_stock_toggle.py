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
