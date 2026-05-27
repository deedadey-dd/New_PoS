from django.test import TestCase, Client
from django.urls import reverse
from apps.core.models import Tenant, User, Role, Location
from apps.sales.models import Shift
from decimal import Decimal

class ShiftProcessTests(TestCase):
    def setUp(self):
        self.client = Client()
        
        # Setup basic data
        self.tenant = Tenant.objects.create(name="Test Tenant", is_active=True)
        self.shop = Location.objects.create(
            tenant=self.tenant, 
            name="Main Shop", 
            location_type="SHOP", 
            is_active=True
        )
        self.role_attendant, _ = Role.objects.get_or_create(name="SHOP_ATTENDANT")
        self.attendant = User.objects.create_user(
            email="attendant@test.com",
            password="password",
            tenant=self.tenant,
            role=self.role_attendant,
            location=self.shop
        )
        
    def test_open_shift(self):
        """Test the process of opening a shift via the view."""
        self.client.force_login(self.attendant)
        
        # Open shift with 50 cash
        response = self.client.post(reverse('sales:shift_open'), {
            'opening_cash': '50.00'
        })
        self.assertRedirects(response, reverse('sales:pos'))
        
        # Verify shift is created
        shift = Shift.objects.filter(attendant=self.attendant, status='OPEN').first()
        self.assertIsNotNone(shift)
        self.assertEqual(shift.opening_cash, Decimal('50.00'))
        self.assertEqual(shift.shop, self.shop)

    def test_close_shift(self):
        """Test closing an open shift via the view."""
        # Create an open shift
        shift = Shift.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.attendant,
            opening_cash=Decimal('50.00'),
            status='OPEN'
        )
        
        self.client.force_login(self.attendant)
        
        # Close shift with 100 cash (indicating 50 sales)
        response = self.client.post(reverse('sales:shift_close', args=[shift.id]), {
            'closing_cash': '100.00',
            'notes': 'End of day'
        })
        self.assertRedirects(response, reverse('core:dashboard'))
        
        # Verify shift is closed
        shift.refresh_from_db()
        self.assertEqual(shift.status, 'CLOSED')
        self.assertEqual(shift.closing_cash, Decimal('100.00'))
        self.assertEqual(shift.notes, 'End of day')
        self.assertIsNotNone(shift.end_time)
