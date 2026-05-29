from django.test import TestCase
from django.urls import reverse
from apps.core.models import Tenant, Role, User, Location

class BulkReceiveTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant")
        self.shop_manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        
        # Enable bulk receive for shop manager
        self.tenant.shop_manager_can_receive_stock = True
        self.tenant.save()

        # Admin user
        self.admin_user = User.objects.create_user(
            email="admin@test.com", password="password123",
            tenant=self.tenant, role=self.admin_role
        )

        # Shop Manager User (without location)
        self.manager_no_loc = User.objects.create_user(
            email="manager1@test.com", password="password123",
            tenant=self.tenant, role=self.shop_manager_role
        )

        # Shop Manager User (with location)
        self.shop_loc = Location.objects.create(
            name="Main Shop", tenant=self.tenant, location_type="SHOP"
        )
        self.manager_with_loc = User.objects.create_user(
            email="manager2@test.com", password="password123",
            tenant=self.tenant, role=self.shop_manager_role,
            location=self.shop_loc
        )

    def test_bulk_receive_location_restriction_for_shop_manager(self):
        """
        Shop Manager with a location should see ONLY their location.
        """
        self.client.force_login(self.manager_with_loc)
        response = self.client.get(reverse('inventory:batch_bulk_receive'))
        self.assertEqual(response.status_code, 200)
        
        # Check locations passed to context
        locations = response.context['locations']
        self.assertEqual(locations.count(), 1)
        self.assertEqual(locations.first().id, self.shop_loc.id)

    def test_bulk_receive_no_location_for_shop_manager(self):
        """
        Shop Manager WITHOUT a location should get an empty location queryset.
        """
        self.client.force_login(self.manager_no_loc)
        response = self.client.get(reverse('inventory:batch_bulk_receive'))
        self.assertEqual(response.status_code, 200)
        
        locations = response.context['locations']
        self.assertEqual(locations.count(), 0)

    def test_bulk_receive_for_admin(self):
        """
        Admin should see STORES and PRODUCTION locations.
        """
        Location.objects.create(name="Store", tenant=self.tenant, location_type="STORES")
        Location.objects.create(name="Production", tenant=self.tenant, location_type="PRODUCTION")
        
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('inventory:batch_bulk_receive'))
        self.assertEqual(response.status_code, 200)
        
        locations = response.context['locations']
        self.assertEqual(locations.count(), 2)
        
        # Should not see the shop location
        self.assertNotIn(self.shop_loc, locations)
