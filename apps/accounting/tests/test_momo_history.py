from django.test import TestCase
from django.urls import reverse
from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Sale
from apps.customers.models import Customer, CustomerTransaction

class MomoHistoryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant")
        self.accountant_role, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        self.shop_manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        
        self.shop = Location.objects.create(name="Shop 1", tenant=self.tenant, location_type='SHOP')
        self.other_shop = Location.objects.create(name="Shop 2", tenant=self.tenant, location_type='SHOP')
        
        self.accountant_user = User.objects.create_user(
            email="acc@test.com", password="password123",
            tenant=self.tenant, role=self.accountant_role
        )
        
        self.shop_manager = User.objects.create_user(
            email="manager@test.com", password="password123",
            tenant=self.tenant, role=self.shop_manager_role,
            location=self.shop
        )
        
        self.customer = Customer.objects.create(name="John Doe", tenant=self.tenant)
        
        # Create some Momo Sales
        Sale.objects.create(
            tenant=self.tenant, shop=self.shop, amount_paid=100.0, 
            status='COMPLETED', payment_method='MOMO', sale_number="SL-01",
            attendant=self.shop_manager
        )
        Sale.objects.create(
            tenant=self.tenant, shop=self.other_shop, amount_paid=200.0, 
            status='COMPLETED', payment_method='MOMO', sale_number="SL-02",
            attendant=self.shop_manager
        )
        
        # Create some Momo Customer Transactions
        CustomerTransaction.objects.create(
            tenant=self.tenant, customer=self.customer, amount=50.0,
            transaction_type='CREDIT', description='MOMO Payment',
            performed_by=self.shop_manager, balance_before=0.0, balance_after=50.0
        )

    def test_shop_manager_momo_history(self):
        """
        Shop Manager should only see Momo transactions for their own shop,
        and the transactions list should populate correctly (testing the if/else logic fix).
        """
        self.client.force_login(self.shop_manager)
        response = self.client.get(reverse('accounting:shop_momo_history'))
        self.assertEqual(response.status_code, 200)
        
        transactions = response.context['transactions']
        self.assertEqual(len(transactions), 2)  # 1 Sale + 1 CT
        
        # unconfirmed_balance = 100 + 50 = 150 (none confirmed), available_balance = 0
        self.assertIn('unconfirmed_balance', response.context)
        self.assertIn('available_balance', response.context)

    def test_accountant_momo_history_no_shop_filter(self):
        """
        Accountant viewing without a shop filter should see all Momo transactions.
        """
        self.client.force_login(self.accountant_user)
        response = self.client.get(reverse('accounting:shop_momo_history'))
        self.assertEqual(response.status_code, 200)
        
        transactions = response.context['transactions']
        self.assertEqual(len(transactions), 3)  # 2 Sales + 1 CT
        
        # Total = 100 + 200 + 50 = 350
        self.assertIn('unconfirmed_balance', response.context)
        self.assertIn('available_balance', response.context)

    def test_accountant_momo_history_with_shop_filter(self):
        """
        Accountant filtering by a specific shop should see only that shop's Momo transactions.
        """
        self.client.force_login(self.accountant_user)
        response = self.client.get(reverse('accounting:shop_momo_history'), {'shop': self.shop.id})
        self.assertEqual(response.status_code, 200)
        
        transactions = response.context['transactions']
        self.assertEqual(len(transactions), 2)  # 1 Sale + 1 CT (Shop 1)
        self.assertIn('unconfirmed_balance', response.context)
        self.assertIn('available_balance', response.context)
