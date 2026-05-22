from django.test import TestCase
from django.urls import reverse
from decimal import Decimal
from apps.core.models import Tenant, Location, Role, User
from apps.inventory.models import Product, Category, ShopPrice
from apps.notifications.models import Notification

class PricingControlModeTests(TestCase):
    def setUp(self):
        # Create Tenant
        self.tenant = Tenant.objects.create(
            name="Test Tenant", 
            slug="test-tenant",
            pricing_control_mode='SHOP_MANAGER'
        )

        # Create Roles
        self.role_admin, _ = Role.objects.get_or_create(name='ADMIN', defaults={'description': 'Admin'})
        self.role_accountant, _ = Role.objects.get_or_create(name='ACCOUNTANT', defaults={'description': 'Accountant'})
        self.role_shop_manager, _ = Role.objects.get_or_create(name='SHOP_MANAGER', defaults={'description': 'Shop Manager'})

        # Create Locations
        self.hq = Location.objects.create(
            tenant=self.tenant, name='HQ', location_type='HQ'
        )
        self.shop1 = Location.objects.create(
            tenant=self.tenant, name='Shop 1', location_type='SHOP'
        )
        self.shop2 = Location.objects.create(
            tenant=self.tenant, name='Shop 2', location_type='SHOP'
        )

        # Create Users
        self.admin = User.objects.create_user(
            email='admin@test.com', password='password',
            tenant=self.tenant, role=self.role_admin, location=self.hq
        )
        self.accountant = User.objects.create_user(
            email='accountant@test.com', password='password',
            tenant=self.tenant, role=self.role_accountant, location=self.hq
        )
        self.shop_manager = User.objects.create_user(
            email='manager@test.com', password='password',
            tenant=self.tenant, role=self.role_shop_manager, location=self.shop1
        )
        
        # Create Product
        self.category = Category.objects.create(tenant=self.tenant, name='General')
        self.product = Product.objects.create(
            tenant=self.tenant, name='Test Product', category=self.category,
            default_selling_price=Decimal('10.00'), is_active=True
        )

    def test_shop_manager_mode_access(self):
        """Test SHOP_MANAGER mode allows manager to set price, but accountant is blocked/read-only."""
        self.tenant.pricing_control_mode = 'SHOP_MANAGER'
        self.tenant.save()

        # Manager sets price
        self.client.login(email='manager@test.com', password='password')
        url = reverse('inventory:shop_price_set', kwargs={'pk': self.product.pk})
        response = self.client.post(url, {'selling_price': '15.00'})
        self.assertRedirects(response, reverse('inventory:shop_price_list'))
        
        price = ShopPrice.objects.get(product=self.product, location=self.shop1, is_active=True)
        self.assertEqual(price.selling_price, Decimal('15.00'))

        # Accountant tries to set price
        self.client.login(email='accountant@test.com', password='password')
        response = self.client.get(url)
        self.assertRedirects(response, reverse('inventory:shop_price_list'))

    def test_accountant_per_shop_mode(self):
        """Test ACCOUNTANT_PER_SHOP mode."""
        self.tenant.pricing_control_mode = 'ACCOUNTANT_PER_SHOP'
        self.tenant.save()

        # Manager is blocked
        self.client.login(email='manager@test.com', password='password')
        url = reverse('inventory:shop_price_set', kwargs={'pk': self.product.pk})
        response = self.client.get(url)
        self.assertRedirects(response, reverse('inventory:shop_price_list'))

        # Accountant sets price for shop 1
        self.client.login(email='accountant@test.com', password='password')
        url_shop1 = reverse('inventory:shop_price_set', kwargs={'pk': self.product.pk}) + f'?shop={self.shop1.pk}'
        response = self.client.post(url_shop1, {'selling_price': '20.00'})
        self.assertRedirects(response, reverse('inventory:shop_price_list'))
        
        price_shop1 = ShopPrice.objects.get(product=self.product, location=self.shop1, is_active=True)
        self.assertEqual(price_shop1.selling_price, Decimal('20.00'))
        
        # Verify notification was sent to manager
        notif = Notification.objects.filter(user=self.shop_manager, notification_type='PRICE_CHANGE').first()
        self.assertIsNotNone(notif)
        self.assertIn('20.00', notif.message)

    def test_accountant_uniform_mode(self):
        """Test ACCOUNTANT_UNIFORM mode."""
        self.tenant.pricing_control_mode = 'ACCOUNTANT_UNIFORM'
        self.tenant.save()

        # Accountant sets uniform price
        self.client.login(email='accountant@test.com', password='password')
        url = reverse('inventory:shop_price_set', kwargs={'pk': self.product.pk})
        response = self.client.post(url, {'selling_price': '30.00'})
        
        # Verify it applied to all shops
        prices = ShopPrice.objects.filter(product=self.product, is_active=True)
        self.assertEqual(prices.count(), 2) # Shop 1 and Shop 2
        for p in prices:
            self.assertEqual(p.selling_price, Decimal('30.00'))
            
        # Verify notifications sent to all managers (we only have one manager in setup)
        notifs = Notification.objects.filter(user=self.shop_manager, notification_type='PRICE_CHANGE')
        self.assertEqual(notifs.count(), 1)
        self.assertIn('globally updated', notifs.first().message)
