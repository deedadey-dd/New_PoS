from decimal import Decimal
from django.test import TestCase, Client
from apps.core.models import Tenant, User, Role, Location
from apps.inventory.models import Product, Category, StockAdjustment
from apps.notifications.models import Notification, BulletinPost


class StockAdjustmentNotificationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Adjustment Org")
        self.location = Location.objects.create(tenant=self.tenant, name="Shop 1", location_type="SHOP")

        self.mgr_role, _ = Role.objects.get_or_create(name="SHOP_MANAGER")
        self.acct_role, _ = Role.objects.get_or_create(name="ACCOUNTANT")
        self.audit_role, _ = Role.objects.get_or_create(name="AUDITOR")
        self.admin_role, _ = Role.objects.get_or_create(name="ADMIN")

        self.manager = User.objects.create_user(
            email="manager@test.com", password="pw",
            tenant=self.tenant, role=self.mgr_role, location=self.location
        )
        self.accountant = User.objects.create_user(
            email="accountant@test.com", password="pw",
            tenant=self.tenant, role=self.acct_role
        )
        self.auditor = User.objects.create_user(
            email="auditor@test.com", password="pw",
            tenant=self.tenant, role=self.audit_role
        )
        self.admin = User.objects.create_user(
            email="admin@test.com", password="pw",
            tenant=self.tenant, role=self.admin_role
        )

        self.category = Category.objects.create(tenant=self.tenant, name="Cat 1")
        self.product = Product.objects.create(
            tenant=self.tenant, name="Widget A", sku="WID-A", category=self.category,
            default_selling_price=Decimal("20.00")
        )

        self.client = Client()

    def test_stock_adjustment_creates_accountant_notification_and_bulletin(self):
        self.client.force_login(self.manager)

        resp = self.client.post('/inventory/stock/adjustment/', data={
            'product': self.product.pk,
            'location': self.location.pk,
            'quantity': 5,
            'adjustment_type': 'ADJUST',
            'reason': 'Found extra boxes during shelf count'
        })
        self.assertEqual(resp.status_code, 302)

        # 1. Verify StockAdjustment created in PENDING
        adj = StockAdjustment.objects.filter(product=self.product).first()
        self.assertIsNotNone(adj)
        self.assertEqual(adj.status, 'PENDING')
        self.assertEqual(adj.quantity, Decimal('5.00'))

        # 2. Verify Accountant received notification
        acct_notif = Notification.objects.filter(
            tenant=self.tenant,
            user=self.accountant,
            notification_type='STOCK_ADJUSTMENT'
        ).first()
        self.assertIsNotNone(acct_notif)
        self.assertIn("Found extra boxes", acct_notif.message)
        self.assertIn("Widget A", acct_notif.message)

        # 3. Verify Auditor also received notification
        audit_notif = Notification.objects.filter(
            tenant=self.tenant,
            user=self.auditor,
            notification_type='STOCK_ADJUSTMENT'
        ).first()
        self.assertIsNotNone(audit_notif)

        # 4. Verify BulletinPost was created and targeted to accountant/auditor/admin
        bulletin = BulletinPost.objects.filter(
            tenant=self.tenant,
            title__icontains="Widget A"
        ).first()
        self.assertIsNotNone(bulletin)
        self.assertIn("Found extra boxes", bulletin.body)
        self.assertTrue(bulletin.is_pinned)
        targeted_role_names = set(bulletin.target_roles.values_list('name', flat=True))
        self.assertIn('ACCOUNTANT', targeted_role_names)
        self.assertIn('AUDITOR', targeted_role_names)

        # 5. Review adjustment by Auditor (approve)
        self.client.force_login(self.auditor)
        review_resp = self.client.post(f'/inventory/stock/adjustment/{adj.pk}/review/', data={
            'action': 'approve',
            'review_notes': 'Verified on site'
        })
        self.assertEqual(review_resp.status_code, 302)

        adj.refresh_from_db()
        self.assertEqual(adj.status, 'APPROVED')

        # 6. Verify Accountant notified of approval
        acct_approval_notif = Notification.objects.filter(
            tenant=self.tenant,
            user=self.accountant,
            title='Stock Adjustment Approved'
        ).first()
        self.assertIsNotNone(acct_approval_notif)
        self.assertIn("Verified on site", acct_approval_notif.message)
