import uuid
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from django.test.client import RequestFactory
from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Shift, Sale, SaleItem
from apps.accounting.models import (
    CashTransfer, BankTransfer, ExpenditureCategory, ExpenditureRequest, ExpenditureItem
)
from apps.customers.models import Customer, CustomerTransaction
from apps.inventory.models import Category, Product, Batch, InventoryLedger, StockAdjustment
from apps.transfers.models import StockRequest, StockRequestItem, Transfer, TransferItem
from apps.core.context_processors import tenant_context


class WorkflowIntegrationTests(TestCase):
    """
    Comprehensive integration test suite validating both the Standard Sale Workflow
    and the Strict Sale Workflow, including cash-on-hand tracking, cash transfers,
    expenditures, inventory operations, and invoice/payment/dispatch operations.
    """

    def setUp(self):
        # Retrieve or create roles
        self.role_admin, _ = Role.objects.get_or_create(name='ADMIN')
        self.role_shop_manager, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        self.role_shop_attendant, _ = Role.objects.get_or_create(name='SHOP_ATTENDANT')
        self.role_shop_cashier, _ = Role.objects.get_or_create(name='SHOP_CASHIER')
        self.role_accountant, _ = Role.objects.get_or_create(name='ACCOUNTANT')
        self.role_stores_manager, _ = Role.objects.get_or_create(name='STORES_MANAGER')
        self.role_auditor, _ = Role.objects.get_or_create(name='AUDITOR')

        # Create a unique tenant for this test run
        self.unique_suffix = str(uuid.uuid4())[:8]
        self.tenant = Tenant.objects.create(
            name=f"Test Org {self.unique_suffix}",
            slug=f"test-org-{self.unique_suffix}",
            email=f"info@{self.unique_suffix}.com",
            phone="1234567890",
            allow_momo_payments=True,
            use_strict_sales_workflow=False,  # default to standard
        )

        # Create locations
        self.shop_location = Location.objects.create(
            tenant=self.tenant,
            name="Test Shop",
            location_type="SHOP"
        )
        self.stores_location = Location.objects.create(
            tenant=self.tenant,
            name="Test Stores",
            location_type="STORES"
        )
        self.production_location = Location.objects.create(
            tenant=self.tenant,
            name="Test Production",
            location_type="PRODUCTION"
        )

        # Create users with location scoping
        self.admin_user = User.objects.create_user(
            email=f"admin@{self.unique_suffix}.com",
            password="password123",
            tenant=self.tenant,
            role=self.role_admin,
            location=self.shop_location
        )
        self.manager_user = User.objects.create_user(
            email=f"manager@{self.unique_suffix}.com",
            password="password123",
            tenant=self.tenant,
            role=self.role_shop_manager,
            location=self.shop_location
        )
        self.attendant_user = User.objects.create_user(
            email=f"attendant@{self.unique_suffix}.com",
            password="password123",
            tenant=self.tenant,
            role=self.role_shop_attendant,
            location=self.shop_location
        )
        self.cashier_user = User.objects.create_user(
            email=f"cashier@{self.unique_suffix}.com",
            password="password123",
            tenant=self.tenant,
            role=self.role_shop_cashier,
            location=self.shop_location
        )
        self.accountant_user = User.objects.create_user(
            email=f"accountant@{self.unique_suffix}.com",
            password="password123",
            tenant=self.tenant,
            role=self.role_accountant,
            location=self.shop_location
        )
        self.stores_user = User.objects.create_user(
            email=f"stores@{self.unique_suffix}.com",
            password="password123",
            tenant=self.tenant,
            role=self.role_stores_manager,
            location=self.stores_location
        )
        self.auditor_user = User.objects.create_user(
            email=f"auditor@{self.unique_suffix}.com",
            password="password123",
            tenant=self.tenant,
            role=self.role_auditor,
            location=self.stores_location
        )

        # Create product category and product master record
        self.category = Category.objects.create(
            tenant=self.tenant,
            name="Test Category"
        )
        self.product = Product.objects.create(
            tenant=self.tenant,
            sku=f"SKU-{self.unique_suffix}",
            name="Test Product",
            category=self.category,
            default_selling_price=Decimal("100.00"),
            reorder_level=Decimal("5.00")
        )

        # Create batch inventories
        self.shop_batch = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.shop_location,
            batch_number=f"B1-{self.unique_suffix}",
            unit_cost=Decimal("60.00"),
            initial_quantity=Decimal("100.00"),
            current_quantity=Decimal("100.00")
        )
        self.stores_batch = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.stores_location,
            batch_number=f"B2-{self.unique_suffix}",
            unit_cost=Decimal("55.00"),
            initial_quantity=Decimal("50.00"),
            current_quantity=Decimal("50.00")
        )

        # Create expenditure categories
        self.exp_category = ExpenditureCategory.objects.create(
            tenant=self.tenant,
            name="Utilities"
        )

    def get_navbar_context(self, user):
        """Mock context processor request and fetch navbar values."""
        factory = RequestFactory()
        request = factory.get('/')
        request.user = user
        return tenant_context(request)

    def test_01_standard_cash_sales(self):
        """
        Verify standard Cash Sales by Attendants and Shop Managers
        and subsequent reflection of cash-on-hand in navbar (open vs closed shifts).
        """
        # Scenario A: Shiftless cash sales (No shift open)
        sale1 = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("200.00"),
            status="PENDING",
            payment_method="CASH"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale1,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("2.00"),
            unit_price=Decimal("100.00")
        )
        sale1.complete(amount_paid=Decimal("200.00"), payment_method="CASH")

        # Context check for attendant (shiftless cash sale reflected)
        ctx_attendant = self.get_navbar_context(self.attendant_user)
        self.assertEqual(ctx_attendant['cash_on_hand'], Decimal("200.00"))

        # Context check for manager (no own sales completed yet)
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("0.00"))

        # Complete a cash sale for manager
        sale2 = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("150.00"),
            status="PENDING",
            payment_method="CASH"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale2,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.50"),
            unit_price=Decimal("100.00")
        )
        sale2.complete(amount_paid=Decimal("150.00"), payment_method="CASH")

        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("150.00"))

        # Scenario B: Shift Open for Attendant
        shift = Shift.objects.create(
            tenant=self.tenant,
            attendant=self.attendant_user,
            shop=self.shop_location,
            opening_cash=Decimal("50.00"),
            status="OPEN"
        )

        # Attendant context should now reflect shiftless cash (200) + open shift opening cash (50)
        ctx_attendant = self.get_navbar_context(self.attendant_user)
        self.assertEqual(ctx_attendant['cash_on_hand'], Decimal("250.00"))

        # Complete cash sale on the open shift
        sale3 = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            shift=shift,
            total=Decimal("100.00"),
            status="PENDING",
            payment_method="CASH"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale3,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00")
        )
        sale3.complete(amount_paid=Decimal("100.00"), payment_method="CASH")

        # Cash on hand should be 200 (shiftless) + 50 (opening) + 100 (shift sale) = 350
        ctx_attendant = self.get_navbar_context(self.attendant_user)
        self.assertEqual(ctx_attendant['cash_on_hand'], Decimal("350.00"))

    def test_02_momo_sales(self):
        """
        Verify local Momo sales, inventory ledger update, and navbar momo balance reflection.
        """
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("100.00"),
            status="PENDING",
            payment_method="MOMO"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00")
        )
        sale.complete(amount_paid=Decimal("100.00"), payment_method="MOMO")

        # Verify ledger entry
        ledger_entry = InventoryLedger.objects.filter(
            tenant=self.tenant,
            product=self.product,
            location=self.shop_location,
            transaction_type="SALE",
            reference_id=sale.pk
        ).first()
        self.assertIsNotNone(ledger_entry)
        self.assertEqual(ledger_entry.quantity, Decimal("-1.00"))

        # Manager sees 100, Accountant sees 0 momo
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['momo_balance'], Decimal("100.00"))

        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['momo_balance'], Decimal("0.00"))

        # Accountant withdraws Momo payment
        from apps.accounting.models import DigitalFundWithdrawal
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            accountant=self.accountant_user,
            amount=Decimal("100.00"),
            fund_source="MOMO",
            notes="Withdrawal"
        )

        # Manager sees 0, Accountant sees 100
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['momo_balance'], Decimal("0.00"))

        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['momo_balance'], Decimal("100.00"))

    def test_03_ecash_sales(self):
        """
        Verify E-Cash (Paystack) sales, inventory ledger, and navbar ecash balance reflection.
        """
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("300.00"),
            status="PENDING",
            payment_method="ECASH"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("3.00"),
            unit_price=Decimal("100.00")
        )
        sale.complete(amount_paid=Decimal("300.00"), payment_method="ECASH", paystack_ref="ref123")

        # Shop Manager sees unconfirmed
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['ecash_balance'], Decimal("300.00"))

        # Accountant sees 0 unconfirmed ecash
        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['ecash_balance'], Decimal("0.00"))

        # Accountant withdraws E-Cash payment
        from apps.accounting.models import DigitalFundWithdrawal
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            accountant=self.accountant_user,
            amount=Decimal("300.00"),
            fund_source="ECASH",
            notes="Withdrawal"
        )

        # Manager sees 0, Accountant sees 300
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['ecash_balance'], Decimal("0.00"))

        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['ecash_balance'], Decimal("300.00"))

    def test_04_credit_sales(self):
        """
        Verify Credit Purchases update customer debt balance and record debit CustomerTransaction.
        """
        customer = Customer.objects.create(
            tenant=self.tenant,
            name="John Doe",
            credit_limit=Decimal("500.00"),
            current_balance=Decimal("0.00"),
            shop=self.shop_location
        )

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("400.00"),
            status="PENDING",
            payment_method="CASH",
            customer=customer
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("4.00"),
            unit_price=Decimal("100.00")
        )

        # Complete sale with partial payment (100 paid, 300 credit)
        sale.complete(amount_paid=Decimal("100.00"), payment_method="CASH")

        # Customer debt increases
        customer.refresh_from_db()
        self.assertEqual(customer.current_balance, Decimal("300.00"))

        # Verify transaction
        ct = CustomerTransaction.objects.filter(
            tenant=self.tenant,
            customer=customer,
            transaction_type="DEBIT",
            reference_id=sale.sale_number
        ).first()
        self.assertIsNotNone(ct)
        self.assertEqual(ct.amount, Decimal("300.00"))
        self.assertEqual(ct.balance_before, Decimal("0.00"))
        self.assertEqual(ct.balance_after, Decimal("300.00"))

        # Attempt to exceed credit limit (limit is 500, balance 300, new sale wants 300 credit)
        sale_exceed = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("300.00"),
            status="PENDING",
            payment_method="CREDIT",
            customer=customer
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale_exceed,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("3.00"),
            unit_price=Decimal("100.00")
        )

        with self.assertRaises(ValidationError):
            sale_exceed.complete(amount_paid=Decimal("0.00"), payment_method="CREDIT")

    def test_05_transfers_workflow(self):
        """
        Verify cash/digital fund transfers:
        1. Shift Close Auto-transfers from Attendant.
        2. Transfer from Manager to Accountant (deducts manager's cash).
        3. Accountant banks physical cash.
        4. Accountant banks digital (Momo) balance.
        """
        # 1. Open shift and run sale
        shift = Shift.objects.create(
            tenant=self.tenant,
            attendant=self.attendant_user,
            shop=self.shop_location,
            opening_cash=Decimal("50.00"),
            status="OPEN"
        )
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            shift=shift,
            total=Decimal("100.00"),
            status="PENDING",
            payment_method="CASH"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.00"),
            unit_price=Decimal("100.00")
        )
        sale.complete(amount_paid=Decimal("100.00"), payment_method="CASH")

        # Shift close auto-creates pending transfer
        shift.close(closing_cash=Decimal("150.00"))
        
        transfer = CashTransfer.objects.create(
            tenant=self.tenant,
            amount=Decimal("150.00"),
            transfer_type="DEPOSIT",
            from_user=self.attendant_user,
            from_location=self.shop_location,
            to_user=self.manager_user,
            to_location=self.shop_location,
            notes=f"Shift closing deposit - Shift #{shift.pk}",
            status="PENDING"
        )

        # Attendant cash is now 0 (shift closed)
        ctx_attendant = self.get_navbar_context(self.attendant_user)
        self.assertEqual(ctx_attendant['cash_on_hand'], Decimal("0.00"))

        # Manager cash is 0 until transfer is confirmed
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("0.00"))

        # Confirm the transfer
        transfer.status = "CONFIRMED"
        transfer.confirmed_at = timezone.now()
        transfer.confirmed_by = self.manager_user
        transfer.save()

        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("150.00"))

        # 2. Manager transfers to Accountant
        manager_to_acc = CashTransfer.objects.create(
            tenant=self.tenant,
            amount=Decimal("100.00"),
            transfer_type="DEPOSIT",
            from_user=self.manager_user,
            from_location=self.shop_location,
            to_user=self.accountant_user,
            to_location=self.shop_location,
            destination="ACCOUNTANT",
            status="PENDING"
        )

        # Manager has not had deduction yet because status is PENDING
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("150.00"))

        # Confirm transfer
        manager_to_acc.status = "CONFIRMED"
        manager_to_acc.confirmed_at = timezone.now()
        manager_to_acc.confirmed_by = self.accountant_user
        manager_to_acc.save()

        # Manager cash drops to 50
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("50.00"))

        # Accountant cash becomes 100
        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['cash_on_hand'], Decimal("100.00"))

        # 3. Accountant banks physical cash
        BankTransfer.objects.create(
            tenant=self.tenant,
            amount=Decimal("60.00"),
            fund_source="CASH",
            teller_name="John Teller",
            accountant=self.accountant_user
        )

        # Accountant physical cash drops to 40
        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['cash_on_hand'], Decimal("40.00"))

        # 4. Accountant banks digital (Momo) funds
        momo_sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            status="COMPLETED",
            payment_method="MOMO"
        )
        # Accountant momo balance starts at 0
        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['momo_balance'], Decimal("0.00"))
        
        # Withdraw MOMO funds
        from apps.accounting.models import DigitalFundWithdrawal
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            accountant=self.accountant_user,
            amount=Decimal("80.00"),
            fund_source="MOMO",
            notes="Withdrawal"
        )
        
        # Accountant momo balance is now 80
        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['momo_balance'], Decimal("80.00"))

        # Bank some momo
        BankTransfer.objects.create(
            tenant=self.tenant,
            amount=Decimal("50.00"),
            fund_source="MOMO",
            teller_name="John Teller",
            accountant=self.accountant_user
        )

        # Accountant momo balance drops to 30
        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['momo_balance'], Decimal("30.00"))

    def test_06_expenditure_workflow(self):
        """
        Verify expenditure requesting, accountant approval, and cashier/manager cash-on-hand updates.
        """
        # Manager completes a sale to get 200.00 cash-on-hand
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("200.00"),
            status="COMPLETED",
            payment_method="CASH"
        )
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("200.00"))

        # Create expenditure request
        req = ExpenditureRequest.objects.create(
            tenant=self.tenant,
            location=self.shop_location,
            requested_by=self.manager_user,
            status="PENDING",
            notes="Voucher note"
        )
        item = ExpenditureItem.objects.create(
            tenant=self.tenant,
            request=req,
            category=self.exp_category,
            amount=Decimal("50.00"),
            description="Office pens",
            status="PENDING"
        )

        # Accountant approves the expenditure item from shop's cash
        item.approve(user=self.accountant_user, source_of_funds="SHOP_CASH")

        item.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(item.status, "APPROVED")
        self.assertEqual(req.status, "FULLY_APPROVED")

        # Verify a confirmed expenditure transfer was created
        ct = CashTransfer.objects.filter(
            tenant=self.tenant,
            transfer_type="EXPENDITURE",
            status="CONFIRMED",
            from_user=self.manager_user,
            amount=Decimal("50.00")
        ).first()
        self.assertIsNotNone(ct)

        # Manager's cash-on-hand is now 150 (200 - 50)
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("150.00"))

    def test_07_inventory_operations(self):
        """
        Verify stock request, conversion to transfer, sending, receiving,
        and stock adjustments with auditor approval.
        """
        # 1. Stock Request
        req = StockRequest.objects.create(
            tenant=self.tenant,
            requesting_location=self.shop_location,
            supplying_location=self.stores_location,
            requested_by=self.manager_user,
            status="PENDING"
        )
        req_item = StockRequestItem.objects.create(
            tenant=self.tenant,
            request=req,
            product=self.product,
            quantity_requested=Decimal("10.00")
        )

        req.approve(user=self.stores_user)
        self.assertEqual(req.status, "APPROVED")

        transfer = req.convert_to_transfer(user=self.stores_user)
        self.assertEqual(req.status, "CONVERTED")
        self.assertIsNotNone(req.resulting_transfer)
        self.assertEqual(transfer.status, "DRAFT")

        # Link batch and send transfer
        t_item = transfer.items.first()
        t_item.batch = self.stores_batch
        t_item.save()

        initial_stores_qty = self.stores_batch.current_quantity  # 50
        transfer.send(user=self.stores_user)
        self.assertEqual(transfer.status, "SENT")

        self.stores_batch.refresh_from_db()
        self.assertEqual(self.stores_batch.current_quantity, initial_stores_qty - Decimal("10.00"))

        # Receive transfer at Shop
        transfer.receive(
            user=self.manager_user,
            items_received={str(t_item.pk): Decimal("10.00")}
        )
        self.assertEqual(transfer.status, "RECEIVED")

        # Verify shop location has new batch with stores batch number
        shop_new_batch = Batch.objects.filter(
            tenant=self.tenant,
            product=self.product,
            location=self.shop_location,
            batch_number=self.stores_batch.batch_number
        ).first()
        self.assertIsNotNone(shop_new_batch)
        self.assertEqual(shop_new_batch.current_quantity, Decimal("10.00"))

        # 2. Stock Adjustment & Auditor Approval
        adj = StockAdjustment.objects.create(
            tenant=self.tenant,
            product=self.product,
            batch=self.shop_batch,
            location=self.shop_location,
            adjustment_type="ADJUST",
            quantity=Decimal("5.00"),
            reason="Found extra stock on shelf",
            status="PENDING",
            requested_by=self.manager_user
        )

        initial_shop_qty = self.shop_batch.current_quantity  # 100
        adj.approve(user=self.auditor_user, notes="Adjusting stock")

        self.assertEqual(adj.status, "APPROVED")
        self.shop_batch.refresh_from_db()
        self.assertEqual(self.shop_batch.current_quantity, initial_shop_qty + Decimal("5.00"))

    def test_08_strict_sale_workflow(self):
        """
        Verify strict sale workflow:
        1. Attendant creates invoice.
        2. Cashier receives payment (cashier cash updated, not dispatched).
        3. Manager dispatches paid goods.
        """
        # Enable strict workflow
        self.tenant.use_strict_sales_workflow = True
        self.tenant.save()

        # Attendant creates pending invoice
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("120.00"),
            status="PENDING",
            payment_method="PENDING_INVOICE"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.20"),
            unit_price=Decimal("100.00")
        )

        self.assertEqual(sale.status, "PENDING")
        self.assertFalse(sale.is_dispatched)

        # Cashier completes payment
        sale.complete(
            amount_paid=Decimal("120.00"),
            payment_method="CASH",
            cashier=self.cashier_user
        )

        sale.refresh_from_db()
        self.assertEqual(sale.status, "PENDING_DISPATCH")
        self.assertEqual(sale.payment_method, "CASH")
        self.assertEqual(sale.cashier, self.cashier_user)
        # In strict workflow, completing payment does NOT dispatch items
        self.assertFalse(sale.is_dispatched)
        
        # Verify no inventory deducted yet
        ledger_count = InventoryLedger.objects.filter(reference_id=sale.pk).count()
        self.assertEqual(ledger_count, 0)

        # Cashier cash-on-hand context reflects the collected cash
        ctx_cashier = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_cashier['cash_on_hand'], Decimal("120.00"))

        # Shop manager dispatches items
        sale.status = "COMPLETED"
        sale.deduct_inventory()
        sale.is_dispatched = True
        sale.dispatched_at = timezone.now()
        sale.dispatched_by = self.manager_user
        sale.save()

        sale.refresh_from_db()
        self.assertTrue(sale.is_dispatched)
        self.assertEqual(sale.dispatched_by, self.manager_user)
        
        # Verify inventory is deducted now
        ledger_count = InventoryLedger.objects.filter(reference_id=sale.pk).count()
        self.assertEqual(ledger_count, 1)

    def test_09_strict_workflow_expenditure(self):
        """
        Verify expenditure requests and approvals under the strict workflow.
        """
        self.tenant.use_strict_sales_workflow = True
        self.tenant.save()

        # Manager completes a sale to get 300.00 cash-on-hand
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("300.00"),
            status="COMPLETED",
            payment_method="CASH"
        )

        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("300.00"))

        # Create expenditure request
        req = ExpenditureRequest.objects.create(
            tenant=self.tenant,
            location=self.shop_location,
            requested_by=self.manager_user,
            status="PENDING",
            notes="Voucher rent note"
        )
        item = ExpenditureItem.objects.create(
            tenant=self.tenant,
            request=req,
            category=self.exp_category,
            amount=Decimal("100.00"),
            description="Shop rent part",
            status="PENDING"
        )

        # Accountant approves the expenditure item from shop's cash
        item.approve(user=self.accountant_user, source_of_funds="SHOP_CASH")

        item.refresh_from_db()
        self.assertEqual(item.status, "APPROVED")

        # Manager's cash-on-hand is now 200 (300 - 100)
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("200.00"))

    def test_10_accountant_digital_withdrawals(self):
        """
        Verify accountant can withdraw E-Cash and Local Momo from a shop and transfer it to the bank.
        """
        from apps.accounting.models import DigitalFundWithdrawal, BankTransfer
        
        # 1. Create a Momo Sale and an E-Cash Sale for the shop
        Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("150.00"),
            amount_paid=Decimal("150.00"),
            status="COMPLETED",
            payment_method="MOMO"
        )
        
        Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("200.00"),
            amount_paid=Decimal("200.00"),
            status="COMPLETED",
            payment_method="ECASH"
        )
        
        # Verify initial shop balances via logic from views (just raw sums here)
        self.assertEqual(DigitalFundWithdrawal.objects.filter(shop=self.shop_location).count(), 0)
        
        # 2. Accountant withdraws partial Momo (100.00)
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            accountant=self.accountant_user,
            amount=Decimal("100.00"),
            fund_source="MOMO",
            notes="Partial Momo withdrawal"
        )
        
        # 3. Accountant withdraws full E-Cash (200.00)
        DigitalFundWithdrawal.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            accountant=self.accountant_user,
            amount=Decimal("200.00"),
            fund_source="ECASH",
            notes="Full E-Cash withdrawal"
        )
        
        # Verify withdrawals were recorded
        self.assertEqual(DigitalFundWithdrawal.objects.filter(shop=self.shop_location, fund_source="MOMO").count(), 1)
        self.assertEqual(DigitalFundWithdrawal.objects.filter(shop=self.shop_location, fund_source="ECASH").count(), 1)
        
        # 4. Accountant transfers funds to the Bank
        BankTransfer.objects.create(
            tenant=self.tenant,
            accountant=self.accountant_user,
            amount=Decimal("50.00"),
            fund_source="MOMO",
            teller_name="John Doe",
            notes="Momo bank transfer"
        )
        
        BankTransfer.objects.create(
            tenant=self.tenant,
            accountant=self.accountant_user,
            amount=Decimal("200.00"),
            fund_source="ECASH",
            teller_name="Jane Doe",
            notes="E-Cash bank transfer"
        )
        
        # Verify Bank Transfers
        self.assertEqual(BankTransfer.objects.filter(tenant=self.tenant, fund_source="MOMO").count(), 1)
        self.assertEqual(BankTransfer.objects.filter(tenant=self.tenant, fund_source="ECASH").count(), 1)

    def test_11_accountant_stock_adjustment_approval(self):
        """
        Verify accountant can approve stock adjustments when the tenant setting allows it.
        """
        from apps.inventory.models import StockAdjustment
        
        # 1. Create a pending stock adjustment
        adjustment = StockAdjustment.objects.create(
            tenant=self.tenant,
            product=self.product,
            batch=self.shop_batch,
            location=self.shop_location,
            adjustment_type='ADD',
            quantity=Decimal('10'),
            reason='Found extra stock',
            requested_by=self.manager_user,
            status='PENDING'
        )
        
        # 2. Accountant approves the adjustment (requires setting)
        self.tenant.accountants_can_approve_adjustments = True
        self.tenant.save()
        
        # In the view, the Accountant checks the setting. We'll directly call the approve method
        # as if the view logic allowed it.
        adjustment.approve(self.accountant_user, notes='Accountant approved')
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, 'APPROVED')
        self.assertEqual(adjustment.reviewed_by, self.accountant_user)

    def test_12_bank_transfer_cash_exit(self):
        """
        Verify that a confirmed bank transfer deducts from sender's cash but does NOT add to accountant's cash.
        """
        from apps.accounting.models import CashTransfer
        from apps.sales.models import Sale
        
        # Give cashier 100 cash
        Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.cashier_user,
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            status="COMPLETED",
            payment_method="CASH"
        )
        
        # Verify cashier has 100
        ctx_cashier = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_cashier['cash_on_hand'], Decimal("100.00"))
        
        # Cashier transfers 100 to BANK
        transfer = CashTransfer.objects.create(
            tenant=self.tenant,
            amount=Decimal("100.00"),
            transfer_type='DEPOSIT',
            destination='BANK',
            status='PENDING',
            from_user=self.cashier_user,
            from_location=self.shop_location,
            to_user=self.accountant_user  # Accountant will confirm
        )
        
        # Accountant confirms the bank transfer
        transfer.status = 'CONFIRMED'
        transfer.save()
        
        # Verify cashier cash on hand is 0 (it exited)
        ctx_cashier2 = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_cashier2['cash_on_hand'], Decimal("0.00"))
        
        # Verify accountant cash on hand is 0 (it did NOT enter accountant's pocket)
        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['cash_on_hand'], Decimal("0.00"))

    def test_13_shift_payments_on_account(self):
        """
        Verify that payments on account are correctly calculated as part of 
        expected cash for an open shift, and locked in when closed.
        """
        from apps.sales.models import Shift
        from apps.customers.models import Customer, CustomerTransaction
        
        # Create a customer with a balance
        customer = Customer.objects.create(
            tenant=self.tenant,
            name="Jane Doe",
            credit_limit=Decimal("500.00"),
            current_balance=Decimal("200.00"),
            shop=self.shop_location
        )
        
        # Open shift
        shift = Shift.objects.create(
            tenant=self.tenant,
            attendant=self.attendant_user,
            shop=self.shop_location,
            opening_cash=Decimal("50.00"),
            status="OPEN"
        )
        
        # Verify initial expected cash (50)
        self.assertEqual(shift.expected_cash, Decimal("50.00"))
        
        # Record a payment on account
        CustomerTransaction.objects.create(
            tenant=self.tenant,
            customer=customer,
            transaction_type="CREDIT",
            amount=Decimal("100.00"),
            balance_before=Decimal("200.00"),
            balance_after=Decimal("100.00"),
            reference_id="PAYMENT-123",
            description="Payment on account (CASH)",
            performed_by=self.attendant_user
        )
        
        # Verify expected cash includes the payment on account
        self.assertEqual(shift.expected_cash, Decimal("150.00"))
        
        # Close shift
        shift.close(closing_cash=Decimal("150.00"))
        
        # Verify expected cash is still 150 after closing
        shift.refresh_from_db()
        self.assertEqual(shift.expected_cash, Decimal("150.00"))

    def test_14_refund_requests_workflow(self):
        """
        Verify the refund request approval workflow and financial adjustments.
        1. Manager requests refund -> PENDING.
        2. Accountant approves -> refund_always_cash=True -> CashTransfer EXPENDITURE created.
        3. Manager requests refund for MOMO sale.
        4. Admin approves -> refund_always_cash=False -> Negative DigitalFundWithdrawal created.
        """
        from apps.sales.models import RefundRequest
        from apps.accounting.models import CashTransfer, DigitalFundWithdrawal

        # 1. Sale with CASH (refund_always_cash=True by default)
        sale_cash = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("150.00"),
            amount_paid=Decimal("150.00"),
            status="COMPLETED",
            payment_method="CASH"
        )

        # Manager requests refund
        rr_cash = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale_cash,
            requested_by=self.manager_user,
            reason="Customer returning defective item"
        )
        self.assertEqual(rr_cash.status, "PENDING")

        # Accountant approves
        rr_cash.approve(self.accountant_user)

        self.assertEqual(rr_cash.status, "APPROVED")
        sale_cash.refresh_from_db()
        self.assertEqual(sale_cash.status, "REFUNDED")

        # Check financial adjustment: CashTransfer(EXPENDITURE)
        exp_ct = CashTransfer.objects.filter(
            tenant=self.tenant,
            transfer_type="EXPENDITURE",
            amount=Decimal("150.00"),
            notes__icontains=rr_cash.refund_number
        ).first()
        self.assertIsNotNone(exp_ct)
        self.assertEqual(exp_ct.status, "CONFIRMED")
        self.assertEqual(exp_ct.from_user, self.manager_user)  # shop manager deducted

        # 2. Sale with MOMO and refund_always_cash=False
        self.tenant.refund_always_cash = False
        self.tenant.save()

        sale_momo = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            status="COMPLETED",
            payment_method="MOMO"
        )

        rr_momo = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale_momo,
            requested_by=self.manager_user,
            reason="Changed mind"
        )

        # Admin approves
        rr_momo.approve(self.admin_user)

        self.assertEqual(rr_momo.status, "APPROVED")
        sale_momo.refresh_from_db()
        self.assertEqual(sale_momo.status, "REFUNDED")

        # Check financial adjustment: Negative DigitalFundWithdrawal
        reversal = DigitalFundWithdrawal.objects.filter(
            tenant=self.tenant,
            shop=self.shop_location,
            amount=Decimal("-80.00"),
            fund_source="MOMO",
            notes__icontains=rr_momo.refund_number
        ).first()
        self.assertIsNotNone(reversal)
