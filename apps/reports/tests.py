from decimal import Decimal
from datetime import date, datetime, timezone as dt_tz
from django.db.models import Sum
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Tenant, Location, Role, User
from apps.sales.models import Sale, SaleItem
from apps.inventory.models import Product, Category, InventoryLedger
from apps.accounting.models import BankTransfer
from apps.reports.services.end_of_day import build_management_end_of_day_report


class ManagementEndOfDayReportTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="One Luxury Int. - Plywood", currency="GHS")
        self.shop = Location.objects.create(
            tenant=self.tenant,
            name="Main Plywood Showroom",
            location_type="SHOP"
        )
        self.other_shop = Location.objects.create(
            tenant=self.tenant,
            name="Secondary Branch",
            location_type="SHOP"
        )
        self.role_admin, _ = Role.objects.get_or_create(
            name="ADMIN",
            defaults={"can_view_reports": True}
        )
        self.role_manager, _ = Role.objects.get_or_create(
            name="SHOP_MANAGER",
            defaults={"can_view_reports": True}
        )
        self.admin = User.objects.create_user(
            email="admin@luxury.com",
            password="password",
            tenant=self.tenant,
            role=self.role_admin,
            location=self.shop,
            is_active=True
        )
        self.manager = User.objects.create_user(
            email="manager@luxury.com",
            password="password",
            tenant=self.tenant,
            role=self.role_manager,
            location=self.shop,
            is_active=True
        )

        self.category_board = Category.objects.create(tenant=self.tenant, name="Board")
        self.category_edge = Category.objects.create(tenant=self.tenant, name="Edge Banding")
        self.category_service = Category.objects.create(tenant=self.tenant, name="Service")

        # 16 products matching actual baseline specification
        # (name, category, qty_sold, unit_price, qty_dispatched, stock_left)
        self.product_specs = [
            ("Edging", self.category_edge, Decimal("158"), Decimal("3.50"), Decimal("302"), Decimal("25888")),
            ("6602 edge band", self.category_edge, Decimal("30"), Decimal("2.00"), Decimal("231"), Decimal("754")),
            ("6803 edge band", self.category_edge, Decimal("22"), Decimal("2.00"), None, Decimal("661")),
            ("Marble black edge band", self.category_edge, Decimal("21"), Decimal("2.00"), None, Decimal("1203")),
            ("6805 edge band", self.category_edge, Decimal("20"), Decimal("2.00"), Decimal("6"), Decimal("433")),
            ("G-Black edge band", self.category_edge, Decimal("20"), Decimal("2.00"), Decimal("65"), Decimal("830")),
            ("Cutting", self.category_service, Decimal("13"), Decimal("10.00"), Decimal("19"), Decimal("38754")),
            ("6807 edge band", self.category_edge, Decimal("6"), Decimal("2.00"), None, Decimal("482")),
            ("White Gloss Board", self.category_board, Decimal("6"), Decimal("600.00"), None, Decimal("81")),
            ("1/8 white Board", self.category_board, Decimal("4"), Decimal("180.00"), Decimal("4"), Decimal("125")),
            ("6805 board", self.category_board, Decimal("4"), Decimal("450.00"), Decimal("5"), Decimal("21")),
            ("6602 board", self.category_board, Decimal("2"), Decimal("450.00"), Decimal("15"), Decimal("91")),
            ("Gloss Grey Board", self.category_board, Decimal("2"), Decimal("600.00"), Decimal("2"), Decimal("114")),
            ("SG-Coffee Board", self.category_board, Decimal("1"), Decimal("450.00"), None, Decimal("8")),
            ("6603 board", self.category_board, Decimal("1"), Decimal("450.00"), None, Decimal("99")),
            ("1/8 SG-Coffee Board", self.category_board, Decimal("1"), Decimal("180.00"), None, Decimal("104")),
        ]

        self.products = {}
        for i, (pname, cat, qty, price, disp, stock) in enumerate(self.product_specs):
            prod = Product.objects.create(
                tenant=self.tenant,
                sku=f"SKU-{i+1:03d}",
                name=pname,
                category=cat,
                default_selling_price=price
            )
            self.products[pname] = prod
            
            # Stock ledger entry
            if stock is not None:
                InventoryLedger.objects.create(
                    tenant=self.tenant,
                    location=self.shop,
                    product=prod,
                    transaction_type="IN",
                    quantity=stock
                )
            
            # Dispatch ledger entry
            if disp is not None:
                leg = InventoryLedger.objects.create(
                    tenant=self.tenant,
                    location=self.shop,
                    product=prod,
                    transaction_type="SALE",
                    quantity=-disp
                )
                InventoryLedger.objects.filter(pk=leg.pk).update(created_at=datetime(2026, 10, 26, 11, 0, 0, tzinfo=dt_tz.utc))

        # Create 15 invoices on 2026-10-26 totaling exactly GHS 10,221.00 and 311 units
        self.report_date = date(2026, 10, 26)
        sales_created = []
        for i in range(15):
            s = Sale.objects.create(
                tenant=self.tenant,
                shop=self.shop,
                attendant=self.manager,
                payment_method="CASH",
                status="COMPLETED",
                total=Decimal("0.00"),
                amount_paid=Decimal("0.00")
            )
            # Update created_at date
            Sale.objects.filter(pk=s.pk).update(created_at=datetime(2026, 10, 26, 10, i, 0, tzinfo=dt_tz.utc))
            s.refresh_from_db()
            sales_created.append(s)

        # Distribute the 16 product lines across the 15 sales
        for idx, (pname, cat, qty, price, disp, stock) in enumerate(self.product_specs):
            target_sale = sales_created[idx % 15]
            line_total = qty * price
            SaleItem.objects.create(
                tenant=self.tenant,
                sale=target_sale,
                product=self.products[pname],
                quantity=qty,
                unit_price=price,
                total=line_total
            )

        # Recalculate totals for all 15 sales
        for s in sales_created:
            stotal = SaleItem.objects.filter(sale=s).aggregate(t=Sum('total'))['t'] or Decimal('0.00')
            Sale.objects.filter(pk=s.pk).update(total=stotal, amount_paid=stotal)

        # Bank transfer of GHS 17,164.50
        bt = BankTransfer.objects.create(
            tenant=self.tenant,
            accountant=self.admin,
            amount=Decimal("17164.50"),
            fund_source="CASH",
            teller_name="Main Bank Teller",
            notes="TXN-BANK-001"
        )
        BankTransfer.objects.filter(pk=bt.pk).update(created_at=datetime(2026, 10, 26, 14, 0, 0, tzinfo=dt_tz.utc))

        self.client = Client()

    def test_management_end_of_day_service_calculations_strict_workflow(self):
        """Verify baseline values, reconciliation status, merchandising metrics, and dispatch in strict workflow."""
        self.tenant.use_strict_sales_workflow = True
        self.tenant.save()

        report = build_management_end_of_day_report(
            tenant=self.tenant,
            start_date=self.report_date,
            end_date=self.report_date,
            shop=self.shop,
            user=self.admin
        )

        # Sales KPIs
        self.assertEqual(report['net_reported_sales'], Decimal('10221.00'))
        self.assertEqual(report['invoice_count'], 15)
        self.assertEqual(report['units_sold'], Decimal('311'))
        self.assertEqual(report['product_rows_count'], 16)
        self.assertEqual(report['cash_share_percent'], Decimal('100.0'))

        # Merchandising
        self.assertEqual(report['top_revenue_sku']['name'], 'White Gloss Board')
        self.assertEqual(report['top_revenue_sku']['revenue'], Decimal('3600.00'))
        self.assertAlmostEqual(float(report['top_revenue_sku']['share']), 35.2, places=1)

        self.assertEqual(report['top_volume_item']['name'], 'Edging')
        self.assertEqual(report['top_volume_item']['units'], Decimal('158'))
        self.assertAlmostEqual(float(report['top_volume_item']['vol_share']), 50.8, places=1)

        self.assertEqual(report['category_highlight']['label'], 'BOARD REVENUE')
        self.assertEqual(report['category_highlight']['revenue'], Decimal('9300.00'))
        self.assertAlmostEqual(float(report['category_highlight']['share']), 91.0, places=1)

        # Financial Movements & Bank Transfer Over-sales
        self.assertEqual(report['funds_to_bank'], Decimal('17164.50'))
        self.assertEqual(report['bank_over_sales_diff'], Decimal('6943.50'))
        self.assertEqual(report['reconciliation_status'], 'INSUFFICIENT_DATA')
        self.assertIn("exceeds today's sales by GHS 6,943.50", report['reconciliation_text'])

        # Daily Controls Match
        self.assertTrue(report['sales_product_match'])
        self.assertEqual(report['product_revenue_sum'], Decimal('10221.00'))
        self.assertEqual(report['product_qty_sum'], Decimal('311'))

        # Operations: Inventory & Dispatch (Strict Workflow)
        self.assertTrue(report['use_strict_sales_workflow'])
        self.assertEqual(report['dispatch_reported_sum'], Decimal('649'))
        self.assertEqual(report['dispatch_numeric_count'], 9)
        self.assertEqual(report['stock_watch_count'], 2)  # SG-Coffee Board (8), 6805 board (21)

    def test_management_end_of_day_standard_workflow_omits_dispatch(self):
        """Standard workflow must NOT track or mention dispatch in report or templates."""
        self.tenant.use_strict_sales_workflow = False
        self.tenant.save()

        report = build_management_end_of_day_report(
            tenant=self.tenant,
            start_date=self.report_date,
            end_date=self.report_date,
            shop=self.shop,
            user=self.admin
        )

        # Workflow flag and dispatch suppression
        self.assertFalse(report['use_strict_sales_workflow'])
        self.assertEqual(report['dispatch_reported_sum'], Decimal('0'))
        self.assertEqual(report['dispatch_numeric_count'], 0)
        self.assertGreater(report['total_stock_units'], Decimal('0'))
        self.assertGreater(report['in_stock_count'], 0)
        self.assertIsNone(report['watchlist_display'][0]['dispatched_txt'])

        self.client.force_login(self.admin)

        # HTML Management Overview check
        resp = self.client.get(
            reverse('reports:end_of_day_management'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'shop_id': self.shop.id}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Inventory visibility")
        self.assertContains(resp, "Total In-Stock Units")
        self.assertNotContains(resp, "Inventory &amp; dispatch visibility")
        self.assertNotContains(resp, "Inventory & dispatch visibility")
        self.assertNotContains(resp, "Dispatch Reported")
        self.assertNotContains(resp, "Quantity Sold vs. Dispatched:")
        self.assertNotContains(resp, "Blank dispatch entries preserved as missing data")

        # Standard Detailed Report check
        resp_std = self.client.get(
            reverse('reports:end_of_day_summary'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'view': 'standard'}
        )
        self.assertEqual(resp_std.status_code, 200)
        self.assertNotContains(resp_std, "<th class=\"text-center\">Qty Dispatched</th>")
        self.assertNotContains(resp_std, "or dispatches")

    def test_management_overview_html_views_and_toggles(self):
        """Ensure both views render with 200 and support view switching."""
        self.tenant.use_strict_sales_workflow = True
        self.tenant.save()
        self.client.force_login(self.admin)

        # 1. Dedicated management view URL
        resp_mgmt = self.client.get(
            reverse('reports:end_of_day_management'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'shop_id': self.shop.id}
        )
        self.assertEqual(resp_mgmt.status_code, 200)
        self.assertContains(resp_mgmt, "Sales & controls dashboard")
        self.assertContains(resp_mgmt, "Product performance")
        self.assertContains(resp_mgmt, "Inventory &amp; dispatch visibility")
        self.assertContains(resp_mgmt, "Complete product detail")

        # 2. Query param view=management on end_of_day_summary URL
        resp_param = self.client.get(
            reverse('reports:end_of_day_summary'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'view': 'management'}
        )
        self.assertEqual(resp_param.status_code, 200)
        self.assertContains(resp_param, "Sales & controls dashboard")

        # 3. Standard view URL
        resp_std = self.client.get(
            reverse('reports:end_of_day_summary'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'view': 'standard'}
        )
        self.assertEqual(resp_std.status_code, 200)
        self.assertContains(resp_std, "Management Overview")

    def test_pdf_exports_for_both_views(self):
        """Ensure both Management PDF and Standard PDF download cleanly."""
        self.client.force_login(self.admin)

        # Download Management Overview PDF
        resp_pdf_mgmt = self.client.get(
            reverse('reports:end_of_day_management'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'pdf': 'true', 'format': 'management'}
        )
        self.assertEqual(resp_pdf_mgmt.status_code, 200)
        self.assertEqual(resp_pdf_mgmt['Content-Type'], 'application/pdf')
        self.assertIn('management_end_of_day_2026-10-26.pdf', resp_pdf_mgmt['Content-Disposition'])

        # Download Standard PDF from management URL
        resp_pdf_std = self.client.get(
            reverse('reports:end_of_day_management'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'pdf': 'true', 'format': 'standard'}
        )
        self.assertEqual(resp_pdf_std.status_code, 200)
        self.assertEqual(resp_pdf_std['Content-Type'], 'application/pdf')
        self.assertIn('end_of_day_report_2026-10-26.pdf', resp_pdf_std['Content-Disposition'])

        # Download Management PDF from standard URL
        resp_pdf_switch = self.client.get(
            reverse('reports:end_of_day_summary'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'pdf': 'true', 'format': 'management'}
        )
        self.assertEqual(resp_pdf_switch.status_code, 200)
        self.assertEqual(resp_pdf_switch['Content-Type'], 'application/pdf')
        self.assertIn('management_end_of_day_2026-10-26.pdf', resp_pdf_switch['Content-Disposition'])

    def test_shop_scoping_isolation(self):
        """Shop manager scoped to shop 1 cannot view secondary branch."""
        self.client.force_login(self.manager)

        # Accessing with shop_id of other shop should default back to manager's shop
        resp = self.client.get(
            reverse('reports:end_of_day_management'),
            {'start_date': '2026-10-26', 'end_date': '2026-10-26', 'shop_id': self.other_shop.id}
        )
        self.assertEqual(resp.status_code, 200)
        # Check report shop is manager's shop
        report = resp.context['report']
        self.assertEqual(report['shop_name'], self.shop.name)
