import json
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Shift, Sale
from apps.inventory.models import Product, Category, InventoryLedger, ShopPrice

class DiscountParameterTests(TestCase):
    """
    Tests to ensure that discount_amount passed from the POS interface
    is properly recorded and deducted from the total during checkout.
    """

    def setUp(self):
        # Create Tenant
        self.tenant = Tenant.objects.create(
            name="Discount Test Tenant",
            slug="discount-test-tenant",
            email="discount@test.com"
        )
        
        # Create Role
        self.attendant_role, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')
        
        # Create Location
        self.shop = Location.objects.create(
            tenant=self.tenant,
            name="Discount Shop",
            location_type="SHOP"
        )
        
        # Create User
        self.user = User.objects.create_user(
            email="attendant@discount.com",
            password="password123",
            tenant=self.tenant,
            role=self.attendant_role,
            location=self.shop
        )
        
        # Create Category & Product
        self.category = Category.objects.create(tenant=self.tenant, name="General")
        self.product = Product.objects.create(
            tenant=self.tenant,
            name="Test Item",
            sku="TST-001",
            category=self.category
        )
        ShopPrice.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
            selling_price=Decimal('100.00'),
            is_active=True
        )
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
            quantity=Decimal('100'),
            transaction_type='INITIAL'
        )

        # Open Shift
        self.shift = Shift.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.user,
            start_time=timezone.now(),
            opening_cash=Decimal('100.00'),
            status='OPEN'
        )
        
        self.client.login(email="attendant@discount.com", password="password123")

    def test_api_checkout_with_discount(self):
        """Test standard checkout API correctly applies discount_amount"""
        # Selling 2 items at 100 each = 200. Discount = 20
        payload = {
            'cart': [
                {
                    'product_id': self.product.id,
                    'quantity': 2,
                    'unit_price': '100.00'
                }
            ],
            'amount_paid': '180.00',
            'payment_method': 'CASH',
            'discount_amount': '20.00'
        }
        
        response = self.client.post(
            reverse('sales:api_checkout'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertTrue(response_data.get('success'))
        
        sale = Sale.objects.get(id=response_data['sale_id'])
        self.assertEqual(sale.subtotal, Decimal('200.00'))
        self.assertEqual(sale.discount_amount, Decimal('20.00'))
        self.assertEqual(sale.total, Decimal('180.00'))

    def test_api_sync_offline_sales_with_discount(self):
        """Test offline sync API correctly applies discount_amount"""
        # Selling 1 item at 100. Discount = 5
        payload = {
            'client_sale_id': 'local-12345',
            'items': [
                {
                    'product_id': self.product.id,
                    'quantity': 1,
                    'unit_price': '100.00'
                }
            ],
            'amount_paid': '95.00',
            'payment_method': 'CASH',
            'discount_amount': '5.00',
            'offline_created_at': timezone.now().isoformat()
        }
        
        response = self.client.post(
            reverse('sales:api_sync_offline_sales'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        
        if response.status_code != 200:
            print("SYNC ERROR:", response.json())
        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertTrue(response_data.get('success'))
        
        sale = Sale.objects.get(sale_number=response_data['sale_number'], tenant=self.tenant)
        self.assertEqual(sale.subtotal, Decimal('100.00'))
        self.assertEqual(sale.discount_amount, Decimal('5.00'))
        self.assertEqual(sale.total, Decimal('95.00'))
