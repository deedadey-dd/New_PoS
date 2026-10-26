"""
Tests for transfer and stock request sequential numbering and collision resilience.
"""
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.db import IntegrityError, transaction

from apps.core.models import Tenant, Location, Role, User
from apps.inventory.models import Product
from apps.transfers.models import Transfer, StockRequest, StockWriteOff


class SequentialNumberingTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Transfer Test Tenant',
            slug='transfer-test-tenant'
        )
        self.role_manager, _ = Role.objects.get_or_create(name='SHOP_MANAGER', defaults={'description': 'Manager'})
        self.role_stores, _ = Role.objects.get_or_create(name='STORES_MANAGER', defaults={'description': 'Stores'})

        self.shop = Location.objects.create(
            tenant=self.tenant,
            name='Shop 1',
            location_type='SHOP',
            is_active=True
        )
        self.stores = Location.objects.create(
            tenant=self.tenant,
            name='Stores 1',
            location_type='STORES',
            is_active=True
        )

        self.shop_manager = User.objects.create_user(
            email='shop_manager@test.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_manager,
            location=self.shop
        )
        self.stores_manager = User.objects.create_user(
            email='stores_manager@test.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_stores,
            location=self.stores
        )

        self.product = Product.objects.create(
            tenant=self.tenant,
            name='Test Product',
            sku='TP-001',
            default_selling_price=Decimal('10.00'),
            is_active=True
        )

    def test_stock_request_sequential_generation(self):
        """StockRequest creates sequential numbers REQ000001, REQ000002, etc."""
        req1 = StockRequest.objects.create(
            tenant=self.tenant,
            requesting_location=self.shop,
            supplying_location=self.stores,
            requested_by=self.shop_manager
        )
        self.assertEqual(req1.request_number, 'REQ000001')

        req2 = StockRequest.objects.create(
            tenant=self.tenant,
            requesting_location=self.shop,
            supplying_location=self.stores,
            requested_by=self.shop_manager
        )
        self.assertEqual(req2.request_number, 'REQ000002')

    def test_stock_request_avoids_existing_gap(self):
        """If REQ000265 already exists, next generation picks REQ000266 instead of failing."""
        # Manually create REQ000265
        StockRequest.objects.create(
            tenant=self.tenant,
            request_number='REQ000265',
            requesting_location=self.shop,
            supplying_location=self.stores,
            requested_by=self.shop_manager
        )

        # Create a new request without specifying request_number
        new_req = StockRequest.objects.create(
            tenant=self.tenant,
            requesting_location=self.shop,
            supplying_location=self.stores,
            requested_by=self.shop_manager
        )
        self.assertEqual(new_req.request_number, 'REQ000266')

    def test_stock_request_collision_retry(self):
        """Simulate a race condition collision on the first attempt, verify retry succeeds."""
        StockRequest.objects.create(
            tenant=self.tenant,
            requesting_location=self.shop,
            supplying_location=self.stores,
            requested_by=self.shop_manager
        )  # Generates REQ000001

        original_generate = StockRequest.generate_request_number
        calls = 0

        def flaky_generate(instance):
            nonlocal calls
            calls += 1
            if calls == 1:
                # First attempt deliberately returns the existing number to trigger IntegrityError
                return 'REQ000001'
            return original_generate(instance)

        with patch.object(StockRequest, 'generate_request_number', side_effect=flaky_generate, autospec=True):
            req = StockRequest.objects.create(
                tenant=self.tenant,
                requesting_location=self.shop,
                supplying_location=self.stores,
                requested_by=self.shop_manager
            )

        self.assertEqual(req.request_number, 'REQ000002')
        self.assertEqual(calls, 2)

    def test_transfer_sequential_generation_and_gap_avoidance(self):
        """Transfer auto-generates TRF000001 and increments properly."""
        t1 = Transfer.objects.create(
            tenant=self.tenant,
            source_location=self.stores,
            destination_location=self.shop,
            created_by=self.stores_manager
        )
        self.assertEqual(t1.transfer_number, 'TRF000001')

        # Insert TRF000100
        Transfer.objects.create(
            tenant=self.tenant,
            transfer_number='TRF000100',
            source_location=self.stores,
            destination_location=self.shop,
            created_by=self.stores_manager
        )

        t3 = Transfer.objects.create(
            tenant=self.tenant,
            source_location=self.stores,
            destination_location=self.shop,
            created_by=self.stores_manager
        )
        self.assertEqual(t3.transfer_number, 'TRF000101')

    def test_stock_write_off_sequential_generation(self):
        """StockWriteOff auto-generates WO000001 and increments properly."""
        wo1 = StockWriteOff.objects.create(
            tenant=self.tenant,
            location=self.stores,
            product=self.product,
            quantity=Decimal('5'),
            reason='DAMAGED',
            performed_by=self.stores_manager
        )
        self.assertEqual(wo1.writeoff_number, 'WO000001')

        wo2 = StockWriteOff.objects.create(
            tenant=self.tenant,
            location=self.stores,
            product=self.product,
            quantity=Decimal('2'),
            reason='EXPIRED',
            performed_by=self.stores_manager
        )
        self.assertEqual(wo2.writeoff_number, 'WO000002')

    def test_stock_request_create_view_post(self):
        """Verify StockRequestCreateView handles POST creation properly."""
        self.client.force_login(self.shop_manager)
        url = reverse('transfers:stock_request_create')

        post_data = {
            'requesting_location': self.shop.id,
            'supplying_location': self.stores.id,
            'notes': 'Test request submission',
            'items-TOTAL_FORMS': '1',
            'items-INITIAL_FORMS': '0',
            'items-MIN_NUM_FORMS': '0',
            'items-MAX_NUM_FORMS': '1000',
            'items-0-product': self.product.id,
            'items-0-quantity_requested': '15',
            'items-0-notes': 'Urgent'
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        created_req = StockRequest.objects.filter(tenant=self.tenant).latest('id')
        self.assertTrue(created_req.request_number.startswith('REQ'))
        self.assertEqual(created_req.items.count(), 1)
        self.assertEqual(created_req.items.first().quantity_requested, Decimal('15'))
