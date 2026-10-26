"""
Tests for AuditorDashboardView to ensure no N+1 queries occur across locations and users.
"""
from decimal import Decimal
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Tenant, Location, Role, User
from apps.sales.models import Sale
from apps.accounting.models import CashTransfer
from apps.customers.models import Customer, CustomerTransaction


class AuditorDashboardNPlusOneTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Auditor Test Tenant',
            slug='auditor-test-tenant'
        )
        self.role_auditor, _ = Role.objects.get_or_create(name='AUDITOR', defaults={'description': 'Auditor'})
        self.role_manager, _ = Role.objects.get_or_create(name='SHOP_MANAGER', defaults={'description': 'Manager'})
        self.role_attendant, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT', defaults={'description': 'Attendant'})
        self.role_accountant, _ = Role.objects.get_or_create(name='ACCOUNTANT', defaults={'description': 'Accountant'})

        self.auditor = User.objects.create_user(
            email='auditor@test.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_auditor
        )
        self.accountant = User.objects.create_user(
            email='accountant@test.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_accountant
        )

    def _create_shop_with_activity(self, index):
        shop = Location.objects.create(
            tenant=self.tenant,
            name=f'Shop {index}',
            location_type='SHOP',
            is_active=True
        )
        attendant = User.objects.create_user(
            email=f'attendant{index}@test.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_attendant,
            location=shop,
            first_name=f'Attendant',
            last_name=f'{index}'
        )
        customer = Customer.objects.create(
            tenant=self.tenant,
            name=f'Customer {index}',
            phone=f'024000000{index}'
        )

        # Sales
        Sale.objects.create(
            tenant=self.tenant,
            shop=shop,
            attendant=attendant,
            payment_method='CASH',
            status='COMPLETED',
            total=Decimal('100.00'),
            amount_paid=Decimal('100.00'),
            created_at=timezone.now()
        )
        Sale.objects.create(
            tenant=self.tenant,
            shop=shop,
            attendant=attendant,
            payment_method='MIXED',
            status='COMPLETED',
            total=Decimal('80.00'),
            amount_paid=Decimal('50.00'),
            created_at=timezone.now()
        )
        Sale.objects.create(
            tenant=self.tenant,
            shop=shop,
            attendant=attendant,
            payment_method='ECASH',
            status='COMPLETED',
            total=Decimal('60.00'),
            amount_paid=Decimal('60.00'),
            created_at=timezone.now()
        )
        Sale.objects.create(
            tenant=self.tenant,
            shop=shop,
            attendant=attendant,
            payment_method='CREDIT',
            status='COMPLETED',
            total=Decimal('40.00'),
            amount_paid=Decimal('0.00'),
            created_at=timezone.now()
        )

        # Deposits to accountant
        CashTransfer.objects.create(
            tenant=self.tenant,
            from_location=shop,
            from_user=attendant,
            to_user=self.accountant,
            transfer_type='DEPOSIT',
            status='CONFIRMED',
            amount=Decimal('50.00'),
            created_at=timezone.now()
        )

        # Expenditures
        CashTransfer.objects.create(
            tenant=self.tenant,
            from_location=shop,
            from_user=attendant,
            to_user=self.accountant,
            transfer_type='EXPENDITURE',
            status='CONFIRMED',
            amount=Decimal('20.00'),
            created_at=timezone.now()
        )

        # Floats received
        CashTransfer.objects.create(
            tenant=self.tenant,
            to_location=shop,
            from_user=self.accountant,
            to_user=attendant,
            transfer_type='FLOAT',
            status='CONFIRMED',
            amount=Decimal('30.00'),
            created_at=timezone.now()
        )

        # Customer Cash Payment on account
        CustomerTransaction.objects.create(
            tenant=self.tenant,
            customer=customer,
            performed_by=attendant,
            transaction_type='CREDIT',
            amount=Decimal('25.00'),
            description='Payment on Account (CASH)',
            balance_before=Decimal('100.00'),
            balance_after=Decimal('75.00'),
            created_at=timezone.now()
        )

        return shop, attendant

    def test_auditor_dashboard_values_calculation(self):
        """Verify financial calculations in location_summary and sales_by_user."""
        self.client.force_login(self.auditor)
        shop, attendant = self._create_shop_with_activity(1)

        url = reverse('core:auditor_dashboard')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        loc_summary = response.context['location_summary']
        self.assertEqual(len(loc_summary), 1)
        item = loc_summary[0]

        # Total revenue: 100 + 80 + 60 + 40 = 280
        self.assertEqual(item['total_revenue'], Decimal('280.00'))
        # Cash sales: 100 (CASH) + 50 (mixed_paid) = 150
        self.assertEqual(item['cash_sales'], Decimal('150.00'))
        # ECash sales: 60
        self.assertEqual(item['ecash_sales'], Decimal('60.00'))
        # Credit sales: 40
        self.assertEqual(item['credit_sales'], Decimal('40.00'))
        # Deposits: 50
        self.assertEqual(item['deposits'], Decimal('50.00'))
        # Floats received: 30
        self.assertEqual(item['floats_received'], Decimal('30.00'))
        # Est cash on hand = cash_sales (150) + customer_payments (25) + floats_received (30) - deposits (50) - expenditures (20) = 135
        self.assertEqual(item['est_cash_on_hand'], Decimal('135.00'))
        self.assertEqual(item['sale_count'], 4)

        users_summary = response.context['sales_by_user']
        self.assertEqual(len(users_summary), 1)
        u_item = users_summary[0]
        self.assertEqual(u_item['total_revenue'], Decimal('280.00'))
        self.assertEqual(u_item['cash_sales'], Decimal('100.00'))
        self.assertEqual(u_item['ecash_sales'], Decimal('60.00'))
        self.assertEqual(u_item['credit_sales'], Decimal('40.00'))
        self.assertEqual(u_item['sale_count'], 4)

    def test_query_count_does_not_scale_with_shops(self):
        """Verify that query count remains constant regardless of the number of shops."""
        self.client.force_login(self.auditor)
        url = reverse('core:auditor_dashboard')

        # Run with 1 shop
        self._create_shop_with_activity(1)
        with CaptureQueriesContext(connection) as ctx1:
            res1 = self.client.get(url)
            self.assertEqual(res1.status_code, 200)
        q_count_1 = len(ctx1.captured_queries)

        # Add 3 more shops (total 4 shops and 4 attendants)
        for i in range(2, 5):
            self._create_shop_with_activity(i)

        with CaptureQueriesContext(connection) as ctx2:
            res2 = self.client.get(url)
            self.assertEqual(res2.status_code, 200)
        q_count_2 = len(ctx2.captured_queries)

        # Without N+1 queries, q_count_2 should be equal to q_count_1
        self.assertEqual(
            q_count_1, q_count_2,
            f"Query count scaled with shops! 1 shop had {q_count_1} queries, 4 shops had {q_count_2} queries"
        )

    def test_accountant_dashboard_query_count_does_not_scale_with_shops(self):
        """Verify that AccountantDashboardView query count remains constant with shops."""
        self.client.force_login(self.accountant)
        url = reverse('accounting:accountant_dashboard')

        # Run with 1 shop
        self._create_shop_with_activity(1)
        with CaptureQueriesContext(connection) as ctx1:
            res1 = self.client.get(url)
            self.assertEqual(res1.status_code, 200)
        q_count_1 = len(ctx1.captured_queries)

        # Add 3 more shops
        for i in range(2, 5):
            self._create_shop_with_activity(i)

        with CaptureQueriesContext(connection) as ctx2:
            res2 = self.client.get(url)
            self.assertEqual(res2.status_code, 200)
        q_count_2 = len(ctx2.captured_queries)

        self.assertEqual(
            q_count_1, q_count_2,
            f"Accountant query count scaled with shops! 1 shop had {q_count_1} queries, 4 shops had {q_count_2} queries"
        )
