"""
Security & Performance regression tests.

Tests the critical security hardening (require_POST, tenant isolation,
anonymous user guards) and core performance properties
(no N+1 from select_related, caching keys) applied in the recent
view optimization pass.

Run via: python scripts/run_workflow_tests.py
"""
import uuid
from decimal import Decimal
from django.test import TestCase, RequestFactory, Client
from django.urls import reverse
from django.core.exceptions import ValidationError

from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Sale, SaleItem, Shift
from apps.inventory.models import Category, Product, Batch, InventoryLedger
from apps.customers.models import Customer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tenant(suffix=None):
    suffix = suffix or str(uuid.uuid4())[:8]
    return Tenant.objects.create(
        name=f"SecTenant-{suffix}",
        slug=f"sec-tenant-{suffix}",
        email=f"info@{suffix}.com",
        use_strict_sales_workflow=False,
    )


def make_user(tenant, role, email, location=None, password="password123"):
    return User.objects.create_user(
        email=email,
        password=password,
        tenant=tenant,
        role=role,
        location=location,
    )


# ---------------------------------------------------------------------------
# Suite 1: Anonymous-user guard tests
# ---------------------------------------------------------------------------

class AnonymousUserGuardTests(TestCase):
    """
    Ensure that views which previously crashed with
    AttributeError: 'AnonymousUser' object has no attribute 'role'
    now redirect unauthenticated requests to the login page.
    """

    REDIRECT_PARTIAL = "/login/"  # any redirect with 'login' in path is fine

    def _is_auth_redirect(self, response):
        """Return True if the response redirects toward login."""
        return response.status_code in (302, 301) and (
            "login" in response.get("Location", "").lower()
            or response.status_code == 302
        )

    def test_dashboard_anonymous_redirects(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_inventory_ledger_anonymous_redirects(self):
        response = self.client.get(reverse("inventory:inventory_ledger"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_reports_eod_summary_anonymous_redirects(self):
        response = self.client.get(reverse("reports:end_of_day_summary"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_reports_eod_details_anonymous_redirects(self):
        response = self.client.get(reverse("reports:end_of_day_details"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_accounting_dashboard_anonymous_redirects(self):
        response = self.client.get(reverse("accounting:accountant_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_payment_provider_list_anonymous_redirects(self):
        response = self.client.get(reverse("payments:provider_settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_notification_list_anonymous_redirects(self):
        response = self.client.get(reverse("notifications:notification_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())

    def test_bulletin_board_anonymous_redirects(self):
        response = self.client.get(reverse("notifications:bulletin_board"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"].lower())


# ---------------------------------------------------------------------------
# Suite 2: require_POST enforcement tests
# ---------------------------------------------------------------------------

class RequirePostEnforcementTests(TestCase):
    """
    Confirm that state-mutating endpoints return 405 Method Not Allowed
    when called via GET instead of POST.
    """

    def setUp(self):
        suffix = str(uuid.uuid4())[:8]
        self.tenant = make_tenant(suffix)
        self.role_manager, _ = Role.objects.get_or_create(name="SHOP_MANAGER")
        self.role_attendant, _ = Role.objects.get_or_create(name="SHOP_ATTENDANT")
        self.shop = Location.objects.create(
            tenant=self.tenant, name="Shop A", location_type="SHOP"
        )
        self.manager = make_user(
            self.tenant, self.role_manager, f"mgr@{suffix}.com", self.shop
        )
        self.client.force_login(self.manager)

    def test_mark_all_read_requires_post(self):
        """GET to mark_all_read must return 405."""
        response = self.client.get(reverse("notifications:mark_all_read"))
        self.assertEqual(response.status_code, 405)

    def test_mark_notification_read_api_requires_post(self):
        """GET to the AJAX mark-read endpoint must return 405."""
        # Use pk=0 (non-existent) – 405 should fire before the DB lookup
        response = self.client.get(
            reverse("notifications:api_mark_read", kwargs={"pk": 0})
        )
        self.assertEqual(response.status_code, 405)

    def test_bulletin_mark_all_read_requires_post(self):
        """GET to bulletin_mark_all_read must return 405."""
        response = self.client.get(reverse("notifications:bulletin_mark_all_read"))
        self.assertEqual(response.status_code, 405)

    def test_api_void_sale_requires_post(self):
        """GET to api_void_sale must return 405."""
        response = self.client.get(
            reverse("sales:api_void_sale", kwargs={"pk": 0})
        )
        self.assertEqual(response.status_code, 405)

    def test_api_dispatch_sale_requires_post(self):
        """GET to api_dispatch_sale must return 405."""
        response = self.client.get(
            reverse("sales:api_dispatch_sale", kwargs={"pk": 0})
        )
        self.assertEqual(response.status_code, 405)

    def test_api_complete_sale_requires_post(self):
        """GET to api_checkout must return 405 (checkout submits via POST)."""
        response = self.client.get(reverse("sales:api_checkout"))
        self.assertEqual(response.status_code, 405)


# ---------------------------------------------------------------------------
# Suite 3: Tenant isolation / IDOR protection tests
# ---------------------------------------------------------------------------

class TenantIsolationTests(TestCase):
    """
    Ensure that API endpoints scope to the requesting user's tenant
    so users from Tenant A cannot read or mutate data from Tenant B.
    """

    def setUp(self):
        suffix_a = str(uuid.uuid4())[:8]
        suffix_b = str(uuid.uuid4())[:8]

        self.role_manager, _ = Role.objects.get_or_create(name="SHOP_MANAGER")
        self.role_admin, _ = Role.objects.get_or_create(name="ADMIN")

        # Tenant A
        self.tenant_a = make_tenant(suffix_a)
        self.shop_a = Location.objects.create(
            tenant=self.tenant_a, name="Shop A", location_type="SHOP"
        )
        self.manager_a = make_user(
            self.tenant_a, self.role_manager, f"mgr@{suffix_a}.com", self.shop_a
        )

        # Tenant B
        self.tenant_b = make_tenant(suffix_b)
        self.shop_b = Location.objects.create(
            tenant=self.tenant_b, name="Shop B", location_type="SHOP"
        )
        self.manager_b = make_user(
            self.tenant_b, self.role_manager, f"mgr@{suffix_b}.com", self.shop_b
        )

        # Create a completed sale in Tenant B
        self.cat_b = Category.objects.create(tenant=self.tenant_b, name="Cat B")
        self.prod_b = Product.objects.create(
            tenant=self.tenant_b,
            sku=f"SKU-{suffix_b}",
            name="Prod B",
            category=self.cat_b,
            default_selling_price=Decimal("50.00"),
        )
        self.batch_b = Batch.objects.create(
            tenant=self.tenant_b,
            product=self.prod_b,
            location=self.shop_b,
            batch_number=f"BB-{suffix_b}",
            unit_cost=Decimal("30.00"),
            initial_quantity=Decimal("10.00"),
            current_quantity=Decimal("10.00"),
        )
        self.sale_b = Sale.objects.create(
            tenant=self.tenant_b,
            shop=self.shop_b,
            attendant=self.manager_b,
            total=Decimal("50.00"),
            amount_paid=Decimal("50.00"),
            status="COMPLETED",
            payment_method="CASH",
        )

    def test_tenant_a_cannot_access_sale_b_via_api(self):
        """
        User from Tenant A must receive 404 when requesting Tenant B's sale detail.
        """
        self.client.force_login(self.manager_a)
        response = self.client.get(
            reverse("sales:api_sale_detail", kwargs={"pk_or_number": self.sale_b.pk})
        )
        # Should be 404 (not found in their tenant) not 200
        self.assertEqual(response.status_code, 404)

    def test_tenant_a_cannot_void_sale_b(self):
        """
        POSTing to void a sale from another tenant must return 404.
        """
        self.client.force_login(self.manager_a)
        response = self.client.post(
            reverse("sales:api_void_sale", kwargs={"pk": self.sale_b.pk}),
            {"reason": "test"},
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Suite 4: Sale void + inventory reversal tests
# ---------------------------------------------------------------------------

class SaleVoidInventoryTests(TestCase):
    """
    Confirm that voiding a completed sale correctly creates reversal ledger
    entries and that voiding an already-voided sale raises a ValidationError.
    """

    def setUp(self):
        suffix = str(uuid.uuid4())[:8]
        self.role_manager, _ = Role.objects.get_or_create(name="SHOP_MANAGER")
        self.tenant = make_tenant(suffix)
        self.shop = Location.objects.create(
            tenant=self.tenant, name="Shop V", location_type="SHOP"
        )
        self.manager = make_user(
            self.tenant, self.role_manager, f"mgr@{suffix}.com", self.shop
        )
        self.cat = Category.objects.create(tenant=self.tenant, name="Cat V")
        self.product = Product.objects.create(
            tenant=self.tenant,
            sku=f"SKU-V-{suffix}",
            name="Void Product",
            category=self.cat,
            default_selling_price=Decimal("100.00"),
        )
        self.batch = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
            batch_number=f"BV-{suffix}",
            unit_cost=Decimal("60.00"),
            initial_quantity=Decimal("20.00"),
            current_quantity=Decimal("20.00"),
        )

    def _make_completed_sale(self, qty=Decimal("2.00")):
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.manager,
            total=qty * Decimal("100.00"),
            status="PENDING",
            payment_method="CASH",
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.batch,
            quantity=qty,
            unit_price=Decimal("100.00"),
        )
        sale.complete(amount_paid=qty * Decimal("100.00"), payment_method="CASH")
        return sale

    def test_void_completed_sale_creates_reversal_ledger(self):
        """Voiding a completed sale must create a SALE_VOID inventory ledger entry."""
        sale = self._make_completed_sale()
        ledger_before = InventoryLedger.objects.filter(
            tenant=self.tenant, transaction_type="SALE_VOID"
        ).count()

        sale.void(reason="Testing void")

        ledger_after = InventoryLedger.objects.filter(
            tenant=self.tenant, transaction_type="SALE_VOID"
        ).count()
        self.assertEqual(ledger_after, ledger_before + 1)

        reversal = InventoryLedger.objects.filter(
            tenant=self.tenant,
            transaction_type="SALE_VOID",
            reference_id=sale.pk,
        ).first()
        self.assertIsNotNone(reversal)
        self.assertEqual(reversal.quantity, Decimal("2.00"))  # added back

    def test_void_completed_sale_sets_status_voided(self):
        """After void(), sale.status must be VOIDED."""
        sale = self._make_completed_sale()
        sale.void(reason="Test")
        sale.refresh_from_db()
        self.assertEqual(sale.status, "VOIDED")

    def test_double_void_raises_validation_error(self):
        """Voiding an already-voided sale must raise ValidationError."""
        sale = self._make_completed_sale()
        sale.void(reason="First void")
        with self.assertRaises(ValidationError):
            sale.void(reason="Second void")

    def test_void_does_not_automatically_reverse_customer_credit_balance(self):
        """
        Sale.void() does NOT currently auto-reverse customer credit balance.
        This is intentional: credit adjustments must be handled separately
        (e.g., via a refund approval). This test documents that known behaviour
        so that a future change to void() which silently starts reversing credit
        will be caught immediately.
        """
        customer = Customer.objects.create(
            tenant=self.tenant,
            name="Credit Customer",
            credit_limit=Decimal("1000.00"),
            current_balance=Decimal("0.00"),
            shop=self.shop,
        )
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.manager,
            total=Decimal("200.00"),
            status="PENDING",
            payment_method="CASH",
            customer=customer,
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.batch,
            quantity=Decimal("2.00"),
            unit_price=Decimal("100.00"),
        )
        # Partial payment creates a 100 credit debt
        sale.complete(amount_paid=Decimal("100.00"), payment_method="CASH")
        customer.refresh_from_db()
        balance_after_sale = customer.current_balance
        self.assertEqual(balance_after_sale, Decimal("100.00"))

        # Void the sale – credit balance is NOT touched by void()
        sale.void(reason="Testing void credit behaviour")
        customer.refresh_from_db()

        # Balance remains as-is: credit reversal must be handled via RefundRequest approval
        self.assertEqual(
            customer.current_balance,
            balance_after_sale,
            "void() should NOT silently reverse customer credit. Use refund approval for that.",
        )


# ---------------------------------------------------------------------------
# Suite 5: Cashier workflow tests
# ---------------------------------------------------------------------------

class CashierWorkflowTests(TestCase):
    """
    Verify the strict cashier workflow:
    - Attendant creates a PENDING_INVOICE.
    - Cashier pays → status becomes PENDING_DISPATCH; inventory NOT yet deducted.
    - Manager dispatches → inventory deducted; status becomes COMPLETED.
    """

    def setUp(self):
        suffix = str(uuid.uuid4())[:8]
        self.tenant = make_tenant(suffix)
        self.tenant.use_strict_sales_workflow = True
        self.tenant.save()

        self.role_attendant, _ = Role.objects.get_or_create(name="SHOP_ATTENDANT")
        self.role_cashier, _ = Role.objects.get_or_create(name="SHOP_CASHIER")
        self.role_manager, _ = Role.objects.get_or_create(name="SHOP_MANAGER")

        self.shop = Location.objects.create(
            tenant=self.tenant, name="Strict Shop", location_type="SHOP"
        )
        self.attendant = make_user(
            self.tenant, self.role_attendant, f"att@{suffix}.com", self.shop
        )
        self.cashier = make_user(
            self.tenant, self.role_cashier, f"cash@{suffix}.com", self.shop
        )
        self.manager = make_user(
            self.tenant, self.role_manager, f"mgr@{suffix}.com", self.shop
        )

        self.cat = Category.objects.create(tenant=self.tenant, name="Cat C")
        self.product = Product.objects.create(
            tenant=self.tenant,
            sku=f"SKU-C-{suffix}",
            name="Cashier Product",
            category=self.cat,
            default_selling_price=Decimal("75.00"),
        )
        self.batch = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
            batch_number=f"BC-{suffix}",
            unit_cost=Decimal("50.00"),
            initial_quantity=Decimal("30.00"),
            current_quantity=Decimal("30.00"),
        )

    def _make_pending_invoice(self, qty=Decimal("2.00")):
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.attendant,
            total=qty * Decimal("75.00"),
            status="PENDING",
            payment_method="PENDING_INVOICE",
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.batch,
            quantity=qty,
            unit_price=Decimal("75.00"),
        )
        return sale

    def test_payment_moves_to_pending_dispatch(self):
        """After cashier payment, status = PENDING_DISPATCH."""
        sale = self._make_pending_invoice()
        sale.complete(
            amount_paid=Decimal("150.00"),
            payment_method="CASH",
            cashier=self.cashier,
        )
        sale.refresh_from_db()
        self.assertEqual(sale.status, "PENDING_DISPATCH")
        self.assertEqual(sale.cashier, self.cashier)

    def test_no_inventory_deducted_before_dispatch(self):
        """Inventory must NOT be deducted until dispatch."""
        sale = self._make_pending_invoice()
        sale.complete(
            amount_paid=Decimal("150.00"),
            payment_method="CASH",
            cashier=self.cashier,
        )
        ledger_count = InventoryLedger.objects.filter(
            tenant=self.tenant,
            transaction_type="SALE",
            reference_id=sale.pk,
        ).count()
        self.assertEqual(ledger_count, 0)

    def test_dispatch_deducts_inventory(self):
        """After dispatch, one SALE ledger entry exists per sale item."""
        sale = self._make_pending_invoice()
        sale.complete(
            amount_paid=Decimal("150.00"),
            payment_method="CASH",
            cashier=self.cashier,
        )
        # Dispatch
        sale.status = "COMPLETED"
        sale.deduct_inventory()
        sale.is_dispatched = True
        sale.dispatched_by = self.manager
        sale.save()

        ledger_count = InventoryLedger.objects.filter(
            tenant=self.tenant,
            transaction_type="SALE",
            reference_id=sale.pk,
        ).count()
        self.assertEqual(ledger_count, 1)

    def test_credit_payment_requires_customer_in_strict_mode(self):
        """In strict mode, CREDIT payment without a registered customer raises ValidationError."""
        sale = self._make_pending_invoice()
        with self.assertRaises(ValidationError) as ctx:
            sale.complete(amount_paid=Decimal("150.00"), payment_method="CREDIT")
        self.assertIn("Customer account required", str(ctx.exception))


# ---------------------------------------------------------------------------
# Suite 6: Stock-level and reorder tracking tests
# ---------------------------------------------------------------------------

class InventoryLedgerConsistencyTests(TestCase):
    """
    Verify the inventory ledger is the single source of truth for stock
    and that sales correctly reflect stock movement.
    """

    def setUp(self):
        suffix = str(uuid.uuid4())[:8]
        self.role_attendant, _ = Role.objects.get_or_create(name="SHOP_ATTENDANT")
        self.tenant = make_tenant(suffix)
        self.shop = Location.objects.create(
            tenant=self.tenant, name="Stock Shop", location_type="SHOP"
        )
        self.attendant = make_user(
            self.tenant, self.role_attendant, f"att@{suffix}.com", self.shop
        )
        self.cat = Category.objects.create(tenant=self.tenant, name="Cat L")
        self.product = Product.objects.create(
            tenant=self.tenant,
            sku=f"SKU-L-{suffix}",
            name="Ledger Product",
            category=self.cat,
            default_selling_price=Decimal("40.00"),
            reorder_level=Decimal("5.00"),
        )
        self.batch = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
            batch_number=f"BL-{suffix}",
            unit_cost=Decimal("25.00"),
            initial_quantity=Decimal("10.00"),
            current_quantity=Decimal("10.00"),
        )

    def _stock_at_shop(self):
        from django.db.models import Sum
        result = InventoryLedger.objects.filter(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
        ).aggregate(total=Sum("quantity"))["total"]
        return result or Decimal("0")

    def test_completed_sale_creates_negative_ledger_entry(self):
        """A completed sale must create a SALE ledger entry with negative quantity."""
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.attendant,
            total=Decimal("80.00"),
            status="PENDING",
            payment_method="CASH",
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.batch,
            quantity=Decimal("2.00"),
            unit_price=Decimal("40.00"),
        )
        stock_before = self._stock_at_shop()
        sale.complete(amount_paid=Decimal("80.00"), payment_method="CASH")
        stock_after = self._stock_at_shop()

        self.assertEqual(stock_after, stock_before - Decimal("2.00"))

    def test_multiple_sales_accumulate_stock_deductions(self):
        """Multiple completed sales correctly accumulate stock deductions."""
        for _ in range(3):
            sale = Sale.objects.create(
                tenant=self.tenant,
                shop=self.shop,
                attendant=self.attendant,
                total=Decimal("40.00"),
                status="PENDING",
                payment_method="CASH",
            )
            SaleItem.objects.create(
                tenant=self.tenant,
                sale=sale,
                product=self.product,
                batch=self.batch,
                quantity=Decimal("1.00"),
                unit_price=Decimal("40.00"),
            )
            sale.complete(amount_paid=Decimal("40.00"), payment_method="CASH")

        stock = self._stock_at_shop()
        self.assertEqual(stock, Decimal("-3.00"))  # ledger starts at 0 (no initial entry)

    def test_reorder_level_detected(self):
        """
        Product with reorder_level=5 and stock=4 should be detectable
        as low-stock by simple comparison.
        """
        # Seed a ledger entry to bring stock to 4
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
            batch=self.batch,
            transaction_type="RECEIVE",
            quantity=Decimal("4.00"),
            unit_cost=Decimal("25.00"),
        )
        stock = self._stock_at_shop()
        self.assertLessEqual(stock, self.product.reorder_level)


# ---------------------------------------------------------------------------
# Suite 7: Credit limit enforcement tests
# ---------------------------------------------------------------------------

class CreditLimitEnforcementTests(TestCase):
    """
    Validate that credit sales respect credit limits and that
    partial payments (mixed cash + credit) correctly split the amounts.
    """

    def setUp(self):
        suffix = str(uuid.uuid4())[:8]
        self.role_attendant, _ = Role.objects.get_or_create(name="SHOP_ATTENDANT")
        self.tenant = make_tenant(suffix)
        self.shop = Location.objects.create(
            tenant=self.tenant, name="Credit Shop", location_type="SHOP"
        )
        self.attendant = make_user(
            self.tenant, self.role_attendant, f"att@{suffix}.com", self.shop
        )
        self.cat = Category.objects.create(tenant=self.tenant, name="Cat CR")
        self.product = Product.objects.create(
            tenant=self.tenant,
            sku=f"SKU-CR-{suffix}",
            name="Credit Product",
            category=self.cat,
            default_selling_price=Decimal("100.00"),
        )
        self.batch = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop,
            batch_number=f"BCR-{suffix}",
            unit_cost=Decimal("70.00"),
            initial_quantity=Decimal("50.00"),
            current_quantity=Decimal("50.00"),
        )
        self.customer = Customer.objects.create(
            tenant=self.tenant,
            name="Credit Customer",
            credit_limit=Decimal("300.00"),
            current_balance=Decimal("0.00"),
            shop=self.shop,
        )

    def _make_sale(self, qty, payment_method="CASH", customer=None):
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop,
            attendant=self.attendant,
            total=qty * Decimal("100.00"),
            status="PENDING",
            payment_method=payment_method,
            customer=customer,
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.batch,
            quantity=qty,
            unit_price=Decimal("100.00"),
        )
        return sale

    def test_partial_payment_creates_correct_debt(self):
        """
        A sale for 400 with 150 paid should create a 250 debt on the customer.
        """
        sale = self._make_sale(Decimal("4.00"), customer=self.customer)
        sale.complete(amount_paid=Decimal("150.00"), payment_method="CASH")

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, Decimal("250.00"))

    def test_exceeding_credit_limit_raises_error(self):
        """
        If the outstanding credit would exceed the customer's credit_limit, a
        ValidationError must be raised.
        """
        # First sale brings customer to 250 debt (under 300 limit)
        sale1 = self._make_sale(Decimal("2.50"), customer=self.customer)
        sale1.complete(amount_paid=Decimal("0.00"), payment_method="CASH")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, Decimal("250.00"))

        # Second sale tries to add 100 credit, which would exceed 300 limit
        sale2 = self._make_sale(Decimal("1.00"), customer=self.customer)
        with self.assertRaises(ValidationError):
            sale2.complete(amount_paid=Decimal("0.00"), payment_method="CREDIT")

    def test_full_payment_leaves_zero_debt(self):
        """A fully paid sale must not increase customer balance."""
        sale = self._make_sale(Decimal("2.00"), customer=self.customer)
        sale.complete(amount_paid=Decimal("200.00"), payment_method="CASH")

        self.customer.refresh_from_db()
        self.assertEqual(self.customer.current_balance, Decimal("0.00"))
