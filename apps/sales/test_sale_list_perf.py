from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from apps.core.models import Tenant, User, Role
from apps.inventory.models import Location, Product
from apps.sales.models import Sale, SaleItem, RefundRequest, ShopSettings


class SaleListViewPerformanceTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Perf Test Tenant', slug='perf-tenant')
        self.shop = Location.objects.create(tenant=self.tenant, name='Perf Shop', location_type='SHOP')
        self.shop_settings = ShopSettings.objects.create(tenant=self.tenant, shop=self.shop)

        self.admin_role = Role.objects.create(name='ADMIN')
        self.manager_role = Role.objects.create(name='SHOP_MANAGER')
        self.cashier_role = Role.objects.create(name='SHOP_CASHIER')

        self.admin_user = User.objects.create_user(
            email='admin@perf.com',
            password='password123',
            tenant=self.tenant,
            role=self.admin_role,
            location=self.shop
        )
        self.manager_user = User.objects.create_user(
            email='manager@perf.com',
            password='password123',
            tenant=self.tenant,
            role=self.manager_role,
            location=self.shop
        )

        self.product = Product.objects.create(tenant=self.tenant, name='Perf Product', sku='PERF-01')

        # Create 30 sales with items
        self.sales = []
        for i in range(30):
            sale = Sale.objects.create(
                tenant=self.tenant,
                shop=self.shop,
                attendant=self.admin_user,
                total=Decimal('100.00'),
                amount_paid=Decimal('100.00'),
                payment_method='CASH',
                status='COMPLETED',
                is_dispatched=(i % 2 == 0)
            )
            SaleItem.objects.create(
                tenant=self.tenant,
                sale=sale,
                product=self.product,
                quantity=Decimal('2.00'),
                unit_price=Decimal('50.00'),
                dispatched_quantity=Decimal('2.00') if sale.is_dispatched else Decimal('0.00')
            )
            self.sales.append(sale)

        # Create a refund request on one sale
        RefundRequest.objects.create(
            tenant=self.tenant,
            sale=self.sales[0],
            requested_by=self.manager_user,
            status='PENDING',
            reason='Damaged item'
        )

        self.client = Client()

    def test_sale_list_view_renders_fast(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('sales:sale_list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('sales', response.context)
        self.assertEqual(len(response.context['sales']), 25)
        self.assertEqual(response.context['summary_total_count'], 30)
        self.assertEqual(response.context['count_pending_approval'], 1)

    def test_dispatch_list_view_renders_fast(self):
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('sales:dispatch_list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('sales', response.context)
        # 15 dispatched sales
        self.assertEqual(response.context['summary_total_count'], 15)

