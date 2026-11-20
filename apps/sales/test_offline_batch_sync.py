import json
import uuid
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Sale, Shift
from apps.inventory.models import Product, Category, InventoryLedger


class OfflineBatchSyncTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.tenant = Tenant.objects.create(
            name="Offline Sync Tenant",
            slug="offline-sync-tenant",
            email="offline@tenant.com"
        )

        self.shop_manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.attendant_role, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')

        self.shop = Location.objects.create(
            tenant=self.tenant,
            name="Accra Central Shop",
            location_type="SHOP"
        )

        self.user = User.objects.create_user(
            email="manager@shop.com",
            password="password123",
            tenant=self.tenant,
            role=self.shop_manager_role,
            location=self.shop,
            first_name="Kwame",
            last_name="Mensah"
        )

        self.client.login(email="manager@shop.com", password="password123")

        self.category = Category.objects.create(tenant=self.tenant, name="General")
        self.product1 = Product.objects.create(
            tenant=self.tenant,
            name="Peak Milk 170g",
            sku="PEAK-01",
            category=self.category,
            default_selling_price=Decimal("12.00")
        )
        self.product2 = Product.objects.create(
            tenant=self.tenant,
            name="Milo 400g",
            sku="MILO-01",
            category=self.category,
            default_selling_price=Decimal("25.00")
        )

        # Add initial inventory for product1: 10 units, product2: 2 units
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product1,
            location=self.shop,
            quantity=Decimal("10"),
            transaction_type="PURCHASE",
            unit_cost=Decimal("8.00")
        )
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product2,
            location=self.shop,
            quantity=Decimal("2"),
            transaction_type="PURCHASE",
            unit_cost=Decimal("18.00")
        )

    def test_batch_sync_clean_sales(self):
        """Test syncing a batch of valid offline sales."""
        id1 = str(uuid.uuid4())
        id2 = str(uuid.uuid4())

        payload = {
            "sales": [
                {
                    "client_sale_id": id1,
                    "offline_created_at": "2026-11-20T08:00:00Z",
                    "payment_method": "CASH",
                    "amount_paid": "24.00",
                    "discount_amount": "0.00",
                    "items": [
                        {"product_id": self.product1.id, "quantity": 2, "unit_price": "12.00"}
                    ]
                },
                {
                    "client_sale_id": id2,
                    "offline_created_at": "2026-11-20T08:15:00Z",
                    "payment_method": "MOMO",
                    "amount_paid": "12.00",
                    "discount_amount": "0.00",
                    "items": [
                        {"product_id": self.product1.id, "quantity": 1, "unit_price": "12.00"}
                    ]
                }
            ]
        }

        url = reverse('sales:api_sync_offline_batch')
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['synced_count'], 2)
        self.assertEqual(data['conflict_count'], 0)
        self.assertEqual(data['failed_count'], 0)

        # Verify in DB
        s1 = Sale.objects.get(tenant=self.tenant, client_sale_id=id1)
        s2 = Sale.objects.get(tenant=self.tenant, client_sale_id=id2)
        self.assertEqual(s1.payment_method, 'CASH')
        self.assertEqual(s2.payment_method, 'MOMO')
        self.assertFalse(s1.has_sync_conflict)

    def test_batch_sync_idempotency(self):
        """Test that re-sending an already-synced sale in a batch returns already_synced without duplicating."""
        id1 = str(uuid.uuid4())
        payload = {
            "sales": [
                {
                    "client_sale_id": id1,
                    "payment_method": "CASH",
                    "amount_paid": "12.00",
                    "items": [{"product_id": self.product1.id, "quantity": 1, "unit_price": "12.00"}]
                }
            ]
        }
        url = reverse('sales:api_sync_offline_batch')
        # First sync
        r1 = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r1.status_code, 200)

        # Second sync with same client_sale_id
        r2 = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(r2.status_code, 200)
        data = r2.json()
        self.assertEqual(data['synced_count'], 1)
        self.assertEqual(data['results'][0]['status'], 'already_synced')

        # DB has exactly 1 record
        self.assertEqual(Sale.objects.filter(tenant=self.tenant, client_sale_id=id1).count(), 1)

    def test_batch_sync_conflict_detection(self):
        """Test that selling more than available stock flags has_sync_conflict."""
        id_conflict = str(uuid.uuid4())
        # product2 only has 2 units in stock, but offline sold 5
        payload = {
            "sales": [
                {
                    "client_sale_id": id_conflict,
                    "payment_method": "CASH",
                    "amount_paid": "125.00",
                    "items": [{"product_id": self.product2.id, "quantity": 5, "unit_price": "25.00"}]
                }
            ]
        }

        url = reverse('sales:api_sync_offline_batch')
        response = self.client.post(url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data['conflict_count'], 1)
        self.assertTrue(data['results'][0]['has_conflicts'])

        # Check DB
        sale = Sale.objects.get(tenant=self.tenant, client_sale_id=id_conflict)
        self.assertTrue(sale.has_sync_conflict)
        self.assertIn("Milo 400g: sold 5 offline, available in ledger was 2", sale.sync_conflict_notes)

    def test_dashboard_fund_summary_offline_availability(self):
        """Test that DashboardView computes today_cash_sales, today_momo_sales, etc."""
        # Create a completed sale today
        Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.user,
            status='COMPLETED',
            payment_method='CASH',
            total=Decimal("50.00"),
            amount_paid=Decimal("50.00")
        )
        Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.user,
            status='COMPLETED',
            payment_method='MOMO',
            total=Decimal("30.00"),
            amount_paid=Decimal("30.00")
        )

        response = self.client.get(reverse('core:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('today_cash_sales', response.context)
        self.assertIn('today_momo_sales', response.context)
        self.assertEqual(response.context['today_cash_sales'], Decimal("50.00"))
        self.assertEqual(response.context['today_momo_sales'], Decimal("30.00"))

    def test_sale_list_sync_conflict_filter(self):
        """Test filtering sales by ?sync_conflict=1."""
        s_clean = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.user,
            status='COMPLETED',
            payment_method='CASH',
            total=Decimal("20.00"),
            amount_paid=Decimal("20.00"),
            has_sync_conflict=False
        )
        s_conflict = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.user,
            status='COMPLETED',
            payment_method='CASH',
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            has_sync_conflict=True,
            sync_conflict_notes="Stock deficit"
        )

        # Filter by sync_conflict=1
        url = reverse('sales:sale_list') + '?sync_conflict=1'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        sales_in_context = response.context['sales']
        self.assertIn(s_conflict, sales_in_context)
        self.assertNotIn(s_clean, sales_in_context)
