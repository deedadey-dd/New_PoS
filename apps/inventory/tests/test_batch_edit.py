from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Tenant, Role, User, Location
from apps.inventory.models import Product, Batch, Category, BatchEditHistory
from apps.notifications.models import BulletinPost


class BatchEditAccountabilityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant")
        
        self.admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        self.shop_manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.cashier_role, _ = Role.objects.get_or_create(name='SHOP_CASHIER')
        self.accountant_role, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        self.stores_manager_role, _ = Role.objects.get_or_create(name='STORES_MANAGER')

        # Users
        self.manager = User.objects.create_user(
            email="manager@test.com", password="password123",
            tenant=self.tenant, role=self.shop_manager_role
        )
        self.cashier = User.objects.create_user(
            email="cashier@test.com", password="password123",
            tenant=self.tenant, role=self.cashier_role
        )
        self.accountant = User.objects.create_user(
            email="accountant@test.com", password="password123",
            tenant=self.tenant, role=self.accountant_role
        )

        # Inventory Setup
        self.location = Location.objects.create(
            name="Main Shop", tenant=self.tenant, location_type="SHOP"
        )
        self.category = Category.objects.create(name="Beverages", tenant=self.tenant)
        self.product = Product.objects.create(
            tenant=self.tenant,
            name='Test Batch Product',
            sku='BAT-100',
            category=self.category,
            default_selling_price=Decimal('10.00')
        )
        
        self.batch = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location,
            batch_number="TEST-BATCH-001",
            initial_quantity=Decimal('100.00'),
            current_quantity=Decimal('100.00'),
            unit_cost=Decimal('3.00'),
            manufacture_date=timezone.now().date(),
        )
        
        self.edit_url = reverse('inventory:batch_edit', kwargs={'pk': self.batch.pk})

    def test_manager_edit_records_history_but_no_bulletin(self):
        """
        A Shop Manager editing a batch should log the change in BatchEditHistory
        but NOT generate a BulletinPost.
        """
        self.client.force_login(self.manager)
        
        post_data = {
            'batch_number': 'TEST-BATCH-001',
            'unit_cost': '3.50',
            'notes': 'Cost increased'
        }
        
        response = self.client.post(self.edit_url, data=post_data)
        self.assertEqual(response.status_code, 302)  # Success redirect
        
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.unit_cost, Decimal('3.50'))
        
        # Verify history
        history = BatchEditHistory.objects.filter(batch=self.batch)
        self.assertTrue(history.exists())
        
        # Sort by id just to reliably get unit_cost if multiple fields change
        uc_history = history.filter(field_changed='unit_cost').first()
        self.assertIsNotNone(uc_history)
        self.assertEqual(uc_history.old_value, '3.00')
        self.assertEqual(uc_history.new_value, '3.50')
        
        # Verify NO bulletin created
        bulletins = BulletinPost.objects.filter(tenant=self.tenant)
        self.assertFalse(bulletins.exists())

    def test_cashier_edit_records_history_and_creates_bulletin(self):
        """
        A Cashier editing a batch should log the change AND generate a BulletinPost
        targeted to management roles.
        """
        self.client.force_login(self.cashier)
        
        post_data = {
            'batch_number': 'TEST-BATCH-002', # Changed batch number
            'unit_cost': '4.00',             # Changed cost
            'notes': 'I made a mistake',
            'manufacture_date': self.batch.manufacture_date.strftime('%Y-%m-%d'),  # unchanged
        }
        
        response = self.client.post(self.edit_url, data=post_data)
        self.assertEqual(response.status_code, 302)
        
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.unit_cost, Decimal('4.00'))
        
        # Verify history
        history = BatchEditHistory.objects.filter(batch=self.batch)
        self.assertEqual(history.count(), 3) # batch_number, unit_cost, notes
        
        # Verify Bulletin created
        bulletins = BulletinPost.objects.filter(tenant=self.tenant, title__icontains="Batch Edit Alert")
        self.assertEqual(bulletins.count(), 1)
        
        bulletin = bulletins.first()
        self.assertEqual(bulletin.created_by, self.cashier)
        
        # Verify targets
        targets = bulletin.target_roles.all()
        target_role_names = [r.name for r in targets]
        self.assertIn('ACCOUNTANT', target_role_names)
        self.assertIn('SHOP_MANAGER', target_role_names)
        self.assertIn('STORES_MANAGER', target_role_names)
        self.assertNotIn('SHOP_CASHIER', target_role_names)
