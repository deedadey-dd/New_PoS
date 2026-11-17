from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.core.models import Tenant, User, Role
from apps.inventory.models import Location, Product
from apps.notifications.models import Notification, BulletinPost
from apps.sales.models import Sale, SaleItem, RefundRequest
from apps.transfers.models import StockRequest, StockRequestItem, Transfer, TransferItem
from apps.accounting.models import CashTransfer, ExpenditureRequest, ExpenditureItem, ExpenditureCategory


class ActivityBulletinAndNotificationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='Activity Tenant', slug='activity-tenant', currency='GHS')
        self.shop = Location.objects.create(tenant=self.tenant, name='Shop Alpha', location_type='SHOP')
        self.stores = Location.objects.create(tenant=self.tenant, name='Main Stores', location_type='STORES')

        self.role_admin, _ = Role.objects.get_or_create(name='ADMIN')
        self.role_accountant, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        self.role_manager, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.role_cashier, _ = Role.objects.get_or_create(name='SHOP_CASHIER')
        self.role_stores, _ = Role.objects.get_or_create(name='STORES_MANAGER')

        self.admin = User.objects.create_user(
            email='admin@activity.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_admin
        )
        self.accountant = User.objects.create_user(
            email='accountant@activity.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_accountant
        )
        self.shop_manager = User.objects.create_user(
            email='manager@activity.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_manager,
            location=self.shop
        )
        self.cashier = User.objects.create_user(
            email='cashier@activity.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_cashier,
            location=self.shop
        )
        self.stores_manager = User.objects.create_user(
            email='stores@activity.com',
            password='password123',
            tenant=self.tenant,
            role=self.role_stores,
            location=self.stores
        )

        self.product = Product.objects.create(
            tenant=self.tenant,
            name='Test Product',
            sku='TP-01'
        )

    def test_refund_approval_creates_notification_and_bulletin(self):
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.cashier,
            total=Decimal('150.00'),
            amount_paid=Decimal('150.00'),
            payment_method='CASH',
            status='COMPLETED'
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            quantity=Decimal('3.00'),
            unit_price=Decimal('50.00'),
            dispatched_quantity=Decimal('3.00')
        )
        refund_req = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale,
            requested_by=self.shop_manager,
            reason='Customer defect report',
            status='PENDING',
            refund_amount=Decimal('150.00')
        )

        # Clear notifications/bulletins created during setup
        Notification.objects.filter(tenant=self.tenant).delete()
        BulletinPost.objects.filter(tenant=self.tenant).delete()

        # Approve refund
        refund_req.approve(approver=self.accountant)

        # Verify notifications sent to cashier, shop manager, admin
        notifs = Notification.objects.filter(tenant=self.tenant, reference_id=refund_req.pk)
        self.assertTrue(notifs.filter(user=self.shop_manager).exists())
        self.assertTrue(notifs.filter(user=self.cashier).exists())

        # Verify bulletin post created targeted to roles
        bulletin = BulletinPost.objects.filter(tenant=self.tenant, title__icontains=refund_req.refund_number).first()
        self.assertIsNotNone(bulletin)
        target_role_names = set(bulletin.target_roles.values_list('name', flat=True))
        self.assertIn('SHOP_CASHIER', target_role_names)
        self.assertIn('ACCOUNTANT', target_role_names)

    def test_stock_request_approval_creates_notification_and_bulletin(self):
        req = StockRequest.objects.create(
            tenant=self.tenant,
            requesting_location=self.shop,
            supplying_location=self.stores,
            requested_by=self.shop_manager,
            status='PENDING'
        )
        StockRequestItem.objects.create(
            tenant=self.tenant,
            request=req,
            product=self.product,
            quantity_requested=Decimal('10.00')
        )

        Notification.objects.filter(tenant=self.tenant).delete()
        BulletinPost.objects.filter(tenant=self.tenant).delete()

        req.approve(user=self.stores_manager)

        # Check notification for shop manager
        notifs = Notification.objects.filter(tenant=self.tenant, reference_id=req.pk)
        self.assertTrue(notifs.filter(user=self.shop_manager).exists())

        # Check bulletin
        bulletin = BulletinPost.objects.filter(tenant=self.tenant, title__icontains=req.request_number).first()
        self.assertIsNotNone(bulletin)
        self.assertIn(self.shop, bulletin.target_locations.all())

    def test_cash_transfer_confirm_creates_notification_and_bulletin(self):
        ct = CashTransfer.objects.create(
            tenant=self.tenant,
            amount=Decimal('500.00'),
            transfer_type='DEPOSIT',
            status='PENDING',
            from_user=self.shop_manager,
            from_location=self.shop,
            to_user=self.accountant,
            notes='EOD cash deposit'
        )

        Notification.objects.filter(tenant=self.tenant).delete()
        BulletinPost.objects.filter(tenant=self.tenant).delete()

        ct.confirm(user=self.accountant)

        # Sender should have notification
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, user=self.shop_manager).exists())

        # Bulletin post created
        bulletin = BulletinPost.objects.filter(tenant=self.tenant, title__icontains='Cash Transfer Confirmed').first()
        self.assertIsNotNone(bulletin)
        self.assertIn('500', bulletin.body)

    def test_expenditure_approval_creates_notification_and_bulletin(self):
        cat = ExpenditureCategory.objects.create(tenant=self.tenant, name='Utilities')
        voucher = ExpenditureRequest.objects.create(
            tenant=self.tenant,
            location=self.shop,
            requested_by=self.shop_manager,
            status='PENDING'
        )
        item = ExpenditureItem.objects.create(
            tenant=self.tenant,
            request=voucher,
            category=cat,
            description='Electricity Bill',
            amount=Decimal('80.00'),
            source_of_funds='SHOP_CASH',
            status='PENDING'
        )

        Notification.objects.filter(tenant=self.tenant).delete()
        BulletinPost.objects.filter(tenant=self.tenant).delete()

        item.approve(user=self.accountant, source_of_funds='SHOP_CASH')

        # Check notification for manager
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, user=self.shop_manager).exists())

        # Check bulletin
        bulletin = BulletinPost.objects.filter(tenant=self.tenant, title__icontains=voucher.voucher_number).first()
        self.assertIsNotNone(bulletin)
        self.assertIn('Electricity Bill', bulletin.body)
