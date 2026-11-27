from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.core.models import Tenant, User, Role, Location
from apps.notifications.models import Notification, BulletinPost, BulletinRead
from apps.notifications.navigation import resolve_target_url, resolve_action_label
from apps.sales.models import Sale, RefundRequest
from apps.transfers.models import Transfer, StockRequest
from apps.accounting.models import CashTransfer, ExpenditureRequest


class RoleSpecificNavigationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Nav Tenant', slug='nav-tenant', currency='GHS')
        self.shop = Location.objects.create(tenant=self.tenant, name='Shop Beta', location_type='SHOP')
        self.stores = Location.objects.create(tenant=self.tenant, name='Stores Beta', location_type='STORES')

        self.role_admin, _ = Role.objects.get_or_create(name='ADMIN')
        self.role_accountant, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        self.role_manager, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.role_cashier, _ = Role.objects.get_or_create(name='SHOP_CASHIER')
        self.role_attendant, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')
        self.role_stores, _ = Role.objects.get_or_create(name='STORES_MANAGER')
        self.role_auditor, _ = Role.objects.get_or_create(name='AUDITOR')

        self.admin = User.objects.create_user(
            email='admin@nav.com', password='password123',
            tenant=self.tenant, role=self.role_admin
        )
        self.accountant = User.objects.create_user(
            email='accountant@nav.com', password='password123',
            tenant=self.tenant, role=self.role_accountant
        )
        self.manager = User.objects.create_user(
            email='manager@nav.com', password='password123',
            tenant=self.tenant, role=self.role_manager, location=self.shop
        )
        self.cashier = User.objects.create_user(
            email='cashier@nav.com', password='password123',
            tenant=self.tenant, role=self.role_cashier, location=self.shop
        )
        self.attendant = User.objects.create_user(
            email='attendant@nav.com', password='password123',
            tenant=self.tenant, role=self.role_attendant, location=self.shop
        )
        self.stores_mgr = User.objects.create_user(
            email='stores@nav.com', password='password123',
            tenant=self.tenant, role=self.role_stores, location=self.stores
        )
        self.auditor = User.objects.create_user(
            email='auditor@nav.com', password='password123',
            tenant=self.tenant, role=self.role_auditor
        )

    def test_refund_request_role_routing(self):
        """Cashier should be routed to PENDING_PAYOUT, Accountant/Admin to list."""
        notif = Notification.objects.create(
            tenant=self.tenant,
            user=self.cashier,
            title='Refund Approved: REF-001',
            message='Please disburse payout',
            notification_type='REFUND_REQUEST',
            reference_type='RefundRequest',
            reference_id=42
        )
        cashier_url = resolve_target_url(notif, self.cashier)
        self.assertEqual(cashier_url, reverse('sales:refund_request_list') + '?status=PENDING_PAYOUT')
        self.assertEqual(resolve_action_label(notif, self.cashier), 'Disburse Refund Payout')

        accountant_url = resolve_target_url(notif, self.accountant)
        self.assertEqual(accountant_url, reverse('sales:refund_request_list'))
        self.assertEqual(resolve_action_label(notif, self.accountant), 'Review Refund Request')

        manager_url = resolve_target_url(notif, self.manager)
        self.assertEqual(manager_url, reverse('sales:refund_request_list'))
        self.assertEqual(resolve_action_label(notif, self.manager), 'View Refund Status')

    def test_price_change_role_routing(self):
        """Accountant manages pricing, Manager checks price change center, Cashier/Attendant goes to POS."""
        post = BulletinPost.objects.create(
            tenant=self.tenant,
            title='Global Price Change: Paracetamol',
            body='New price 10.00',
            post_type='PRICE_ALERT',
            notification_type='PRICE_CHANGE',
            reference_type='Product',
            reference_id=99
        )
        # Accountant -> Manage pricing
        self.assertEqual(resolve_target_url(post, self.accountant), reverse('inventory:shop_price_list'))
        self.assertEqual(resolve_action_label(post, self.accountant), 'Manage Pricing')

        # Shop Manager -> Price Change Center
        self.assertEqual(resolve_target_url(post, self.manager), reverse('inventory:price_change_center'))
        self.assertEqual(resolve_action_label(post, self.manager), 'Review Price Changes')

        # Cashier -> POS
        self.assertEqual(resolve_target_url(post, self.cashier), reverse('sales:pos'))
        self.assertEqual(resolve_action_label(post, self.cashier), 'Go to POS')

        # Attendant -> POS
        self.assertEqual(resolve_target_url(post, self.attendant), reverse('sales:pos'))

        # Auditor -> Price History
        self.assertEqual(resolve_target_url(post, self.auditor), reverse('accounting:price_history'))

    def test_stock_request_role_routing(self):
        """Stores manager reviews request detail, Shop manager tracks request."""
        notif = Notification.objects.create(
            tenant=self.tenant,
            user=self.stores_mgr,
            title='Stock Request: REQ-101',
            message='Stock requested by Shop Beta',
            notification_type='STOCK_REQUEST',
            reference_type='StockRequest',
            reference_id=101
        )
        expected_url = reverse('transfers:stock_request_detail', kwargs={'pk': 101})
        self.assertEqual(resolve_target_url(notif, self.stores_mgr), expected_url)
        self.assertEqual(resolve_action_label(notif, self.stores_mgr), 'Review Stock Request')

        self.assertEqual(resolve_target_url(notif, self.manager), expected_url)
        self.assertEqual(resolve_action_label(notif, self.manager), 'Track Stock Request')

    def test_expenditure_role_routing(self):
        """Accountant and Shop Manager can review/view voucher."""
        post = BulletinPost.objects.create(
            tenant=self.tenant,
            title='Expenditure Approved: Voucher VCH-55',
            body='Voucher 55 approved',
            post_type='ANNOUNCEMENT',
            notification_type='EXPENDITURE_REQUEST',
            reference_type='ExpenditureRequest',
            reference_id=55
        )
        expected_url = reverse('accounting:expenditure_detail', kwargs={'pk': 55})
        self.assertEqual(resolve_target_url(post, self.accountant), expected_url)
        self.assertEqual(resolve_action_label(post, self.accountant), 'Review Expenditure Voucher')

        self.assertEqual(resolve_target_url(post, self.manager), expected_url)
        self.assertEqual(resolve_action_label(post, self.manager), 'View Expenditure Voucher')

    def test_cash_transfer_role_routing(self):
        """Cash transfers route to cash_transfer_list."""
        notif = Notification.objects.create(
            tenant=self.tenant,
            user=self.accountant,
            title='Cash Transfer Confirmed: GHS 500',
            message='Confirmed by cashier',
            notification_type='CASH_TRANSFER',
            reference_type='CashTransfer',
            reference_id=7
        )
        self.assertEqual(resolve_target_url(notif, self.accountant), reverse('accounting:cash_transfer_list'))
        self.assertEqual(resolve_target_url(notif, self.cashier), reverse('accounting:cash_transfer_list'))

    def test_stock_adjustment_role_routing(self):
        """Stock adjustments route to adjustment_history."""
        notif = Notification.objects.create(
            tenant=self.tenant,
            user=self.accountant,
            title='Stock Adjustment: Product A',
            message='Adjustment recorded',
            notification_type='STOCK_ADJUSTMENT',
            reference_type='StockAdjustment',
            reference_id=12
        )
        self.assertEqual(resolve_target_url(notif, self.accountant), reverse('inventory:adjustment_history'))
        self.assertEqual(resolve_action_label(notif, self.accountant), 'Review Stock Adjustments')

    def test_open_notification_view(self):
        """Visiting notification_open marks notification as read and redirects."""
        notif = Notification.objects.create(
            tenant=self.tenant,
            user=self.cashier,
            title='Refund Approved: REF-999',
            message='Please payout',
            notification_type='REFUND_REQUEST',
            reference_type='RefundRequest',
            reference_id=88
        )
        self.assertFalse(notif.is_read)

        self.client.force_login(self.cashier)
        url = reverse('notifications:notification_open', kwargs={'pk': notif.pk})
        response = self.client.get(url)

        notif.refresh_from_db()
        self.assertTrue(notif.is_read)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/sales/refund-requests/?status=PENDING_PAYOUT', response.url)

    def test_open_bulletin_view(self):
        """Visiting bulletin_open marks bulletin read for user and redirects."""
        post = BulletinPost.objects.create(
            tenant=self.tenant,
            title='Cash Transfer Confirmed: GHS 200',
            body='Transfer completed',
            post_type='ANNOUNCEMENT',
            notification_type='CASH_TRANSFER',
            reference_type='CashTransfer',
            reference_id=10
        )
        self.assertFalse(BulletinRead.objects.filter(bulletin_post=post, user=self.accountant).exists())

        self.client.force_login(self.accountant)
        url = reverse('notifications:bulletin_open', kwargs={'pk': post.pk})
        response = self.client.get(url)

        self.assertTrue(BulletinRead.objects.filter(bulletin_post=post, user=self.accountant).exists())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('accounting:cash_transfer_list'))
