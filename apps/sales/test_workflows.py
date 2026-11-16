import json
import uuid
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from django.test.client import RequestFactory
from django.core.exceptions import ValidationError
from django.db.models import Sum

from apps.core.models import Tenant, Role, User, Location
from apps.sales.models import Shift, Sale, SaleItem, RefundRequest
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

        # Context check for manager (includes the attendant's sale in standard workflow)
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("200.00"))

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
        # Standard workflow: manager sees ALL shop cash (200 attendant + 150 own = 350)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("350.00"))

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
        
        # In the real view, record_payment is called. We need to simulate that.
        from apps.payments.models import ECashLedger
        ECashLedger.record_payment(
            tenant=self.tenant, amount=Decimal('300.00'), sale=sale,
            shop=self.shop_location, user=self.manager_user, paystack_ref="ref123"
        )

        # Shop Manager sees unconfirmed
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['ecash_balance'], Decimal("300.00"))

        # Accountant sees 0 unconfirmed ecash
        ctx_accountant = self.get_navbar_context(self.accountant_user)
        self.assertEqual(ctx_accountant['ecash_balance'], Decimal("0.00"))

        # Accountant withdraws E-Cash payment
        from apps.payments.models import ECashWithdrawal
        withdrawal = ECashWithdrawal.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            amount=Decimal("300.00"),
            withdrawn_by=self.accountant_user,
            notes="Withdrawal"
        )
        withdrawal.complete(self.accountant_user)

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

        # Manager cash is 100 (the sale amount, ignoring the internal 50 float)
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("100.00"))

        # Confirm the transfer
        transfer.status = "CONFIRMED"
        transfer.confirmed_at = timezone.now()
        transfer.confirmed_by = self.manager_user
        transfer.save()

        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("100.00"))

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
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("100.00"))

        # Confirm transfer
        manager_to_acc.status = "CONFIRMED"
        manager_to_acc.confirmed_at = timezone.now()
        manager_to_acc.confirmed_by = self.accountant_user
        manager_to_acc.save()

        # Manager cash drops to 0
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("0.00"))

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

        # Test manager shift opening cash bloat bug (regression test)
        # Manager opens a shift with 100 float
        shift = Shift.objects.create(
            tenant=self.tenant,
            attendant=self.manager_user,
            shop=self.shop_location,
            opening_cash=Decimal("100.00"),
            status="OPEN"
        )
        
        # Cash on hand should STILL be 200 because internal floats do not increase global shop cash
        ctx_manager = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager['cash_on_hand'], Decimal("200.00"))
        
        # Manager closes shift
        shift.close(closing_cash=Decimal("300.00"))
        
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
        self.tenant.enable_refunds = True
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
        self.tenant.enable_refunds = True
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
        1. Manager requests refund for CASH sale -> PENDING.
        2. Accountant approves -> refund_always_cash=True, but since it's a CASH sale, NO EXPENDITURE is created.
           Cash-on-hand must drop by exactly the sale amount once.
        3. Manager requests refund for MOMO sale.
        4. Admin approves -> refund_always_cash=False -> NO DigitalFundWithdrawal created. MOMO drops naturally.
        5. Test Cross-payment refund: ECASH sale, refund_always_cash=True -> EXPENDITURE created, ECASH restored.
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
        
        ctx_before = self.get_navbar_context(self.manager_user)
        cash_before = ctx_before['cash_on_hand']

        # Manager requests refund
        rr_cash = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale_cash,
            requested_by=self.manager_user,
            reason="Customer returning defective item"
        )
        self.assertEqual(rr_cash.status, "PENDING")

        # Accountant approves (authorizes refund, no fund deduction yet)
        rr_cash.approve(self.accountant_user)
        self.assertEqual(rr_cash.status, "APPROVED")
        self.assertEqual(rr_cash.payout_status, "PENDING")

        # Admin executes payout (fund deduction takes effect)
        rr_cash.execute_payout(self.admin_user)
        self.assertEqual(rr_cash.payout_status, "PAID")
        sale_cash.refresh_from_db()
        self.assertEqual(sale_cash.status, "REFUNDED")

        # Check financial adjustment: For CASH, NO CashTransfer(EXPENDITURE) should be created
        exp_ct = CashTransfer.objects.filter(
            tenant=self.tenant,
            transfer_type="EXPENDITURE",
            notes__icontains=rr_cash.refund_number
        ).first()
        self.assertIsNone(exp_ct, "CashTransfer EXPENDITURE should NOT be created for CASH refunds")

        # Verify double-deduction fix: Cash should drop by exactly 150
        ctx_after = self.get_navbar_context(self.manager_user)
        cash_after = ctx_after['cash_on_hand']
        self.assertEqual(cash_after, cash_before - Decimal("150.00"), "Cash-on-hand double deducted!")

        # 2. Sale with MOMO and refund_always_cash=False
        self.tenant.enable_refunds = True
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
        
        ctx_before = self.get_navbar_context(self.manager_user)
        momo_before = ctx_before['momo_balance']

        rr_momo = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale_momo,
            requested_by=self.manager_user,
            reason="Changed mind"
        )

        # Admin approves
        rr_momo.approve(self.admin_user)
        self.assertEqual(rr_momo.status, "APPROVED")
        self.assertEqual(rr_momo.payout_status, "PENDING")

        # Admin executes payout
        rr_momo.execute_payout(self.admin_user)
        self.assertEqual(rr_momo.payout_status, "PAID")
        sale_momo.refresh_from_db()
        self.assertEqual(sale_momo.status, "REFUNDED")

        # Check financial adjustment: NO DigitalFundWithdrawal should be created
        reversal = DigitalFundWithdrawal.objects.filter(
            tenant=self.tenant,
            fund_source="MOMO",
            notes__icontains=rr_momo.refund_number
        ).first()
        self.assertIsNone(reversal, "DigitalFundWithdrawal should NOT be created for same-method refunds")
        
        ctx_after = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_after['momo_balance'], momo_before - Decimal("80.00"))
        
        # 3. Sale with ECASH and refund_always_cash=True (Cross-payment)
        self.tenant.refund_always_cash = True
        self.tenant.save()
        
        sale_ecash = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("120.00"),
            amount_paid=Decimal("120.00"),
            status="COMPLETED",
            payment_method="ECASH"
        )
        
        # Give manager some physical cash so it doesn't floor at 0
        Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.manager_user,
            total=Decimal("200.00"),
            amount_paid=Decimal("200.00"),
            status="COMPLETED",
            payment_method="CASH"
        )
        
        ctx_before = self.get_navbar_context(self.manager_user)
        ecash_before = ctx_before['ecash_balance']
        cash_before = ctx_before['cash_on_hand']
        
        rr_ecash = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale_ecash,
            requested_by=self.manager_user,
            reason="Cross payment test"
        )
        rr_ecash.approve(self.admin_user)
        self.assertEqual(rr_ecash.status, "APPROVED")
        self.assertEqual(rr_ecash.payout_status, "PENDING")
        rr_ecash.execute_payout(self.admin_user)
        self.assertEqual(rr_ecash.payout_status, "PAID")
        
        # Cross payment MUST create both an EXPENDITURE and a DigitalFundWithdrawal restoration
        exp_ct = CashTransfer.objects.filter(
            tenant=self.tenant,
            transfer_type="EXPENDITURE",
            notes__icontains=rr_ecash.refund_number
        ).first()
        self.assertIsNotNone(exp_ct, "EXPENDITURE missing for cross-payment refund")
        
        reversal = DigitalFundWithdrawal.objects.filter(
            tenant=self.tenant,
            fund_source="ECASH",
            notes__icontains=rr_ecash.refund_number
        ).first()
        self.assertIsNotNone(reversal, "ECASH restoration missing for cross-payment refund")
        
        ctx_after = self.get_navbar_context(self.manager_user)
        # ECASH should be unchanged because it was returned via physical cash
        self.assertEqual(ctx_after['ecash_balance'], ecash_before)
        # Physical cash should drop by 120
        self.assertEqual(ctx_after['cash_on_hand'], cash_before - Decimal("120.00"))

    def test_15_strict_workflow_ecash_invoice(self):
        """
        Verify strict sale workflow with E-Cash invoice payment via Paystack inline initialization.
        """
        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
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

        # Call initialize_ecash_payment with existing_sale_id - mock provider so no real Paystack API call
        import unittest.mock as mock
        import json
        from types import SimpleNamespace
        from django.test import RequestFactory
        from apps.sales.views import initialize_ecash_payment, verify_ecash_payment
        from apps.payments.models import PaymentProviderConfig, ShopPaymentAssignment

        # Setup payment provider for shop (needed for verify step DB lookup)
        config = PaymentProviderConfig.objects.create(
            tenant=self.tenant,
            provider='PAYSTACK',
            nickname='Test Paystack',
            is_active=True
        )
        ShopPaymentAssignment.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            provider_config=config,
            priority=1
        )
        
        mock_provider = SimpleNamespace(
            provider_name='Paystack',
            public_key='pk_test_mock',
            get_checkout_type='inline',
        )
        
        factory = RequestFactory()
        request = factory.post('/sales/api/ecash/initialize/', 
            json.dumps({
                'existing_sale_id': sale.pk,
                'provider_config_id': config.pk,
                'total': "120.00"
            }),
            content_type='application/json'
        )
        request.user = self.cashier_user
        
        # Test initialization — mock get_payment_provider to avoid real Paystack credentials
        with mock.patch('apps.payments.services.paystack.get_payment_provider', return_value=mock_provider):
            response = initialize_ecash_payment(request)
        if response.status_code != 200:
            print(f"DEBUG init: {response.content}")
        self.assertEqual(response.status_code, 200)
        
        data = json.loads(response.content)
        self.assertTrue(data.get('success'))
        self.assertEqual(data.get('checkout_type'), 'inline')
        reference = data.get('reference')
        
        sale.refresh_from_db()
        self.assertEqual(sale.paystack_reference, reference)
        self.assertEqual(sale.payment_method, "PENDING_INVOICE")
        self.assertEqual(sale.status, "PENDING") # still pending until verified
        
        # Test verification
        request = factory.post('/sales/api/ecash/verify/',
            json.dumps({
                'sale_id': sale.pk,
                'reference': reference
            }),
            content_type='application/json'
        )
        request.user = self.cashier_user
        
        # Mock both get_payment_provider and verify_payment to avoid real Paystack API calls
        mock_verify_result = SimpleNamespace(success=True, amount=120.00, message='Success', data={})
        mock_provider_verify = SimpleNamespace(
            provider_name='Paystack',
            public_key='pk_test_mock',
            get_checkout_type='inline',
            verify_payment=mock.MagicMock(return_value=mock_verify_result)
        )
        with mock.patch('apps.payments.services.paystack.get_payment_provider', return_value=mock_provider_verify):
            response = verify_ecash_payment(request)
            if response.status_code != 200:
                print(f"DEBUG verify: {response.content}")
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)
            self.assertTrue(data.get('success'))
            
        sale.refresh_from_db()
        self.assertEqual(sale.status, "PENDING_DISPATCH")
        
        # Verify ECashLedger
        from apps.payments.models import ECashLedger
        ledger = ECashLedger.objects.filter(reference_type='Sale', reference_id=sale.pk).first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.amount, Decimal("120.00"))

    def test_14_strict_sale_credit_validation(self):
        """
        Verify that walk-in customers cannot pay by CREDIT in strict sales workflow.
        """
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("120.00"),
            status="PENDING",
            payment_method="PENDING_INVOICE",
            customer=None
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.20"),
            unit_price=Decimal("100.00")
        )

        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as context:
            sale.complete(amount_paid=Decimal("120.00"), payment_method="CREDIT")
            
        self.assertIn("Customer account required for credit/partial payment.", str(context.exception))

    def test_15_credit_payment_increases_balance(self):
        """
        Verify that paying by CREDIT increases the registered customer's debt balance.
        """
        from apps.customers.models import Customer
        customer = Customer.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            name="Test Credit User",
            current_balance=Decimal("0.00")
        )
        
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("150.00"),
            status="PENDING",
            payment_method="PENDING_INVOICE",
            customer=customer
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.50"),
            unit_price=Decimal("100.00")
        )

        sale.complete(amount_paid=Decimal("150.00"), payment_method="CREDIT")
        
        customer.refresh_from_db()
        self.assertEqual(customer.current_balance, Decimal("150.00"))
        
        from apps.customers.models import CustomerTransaction
        txn = CustomerTransaction.objects.filter(customer=customer, transaction_type="DEBIT").first()
        self.assertIsNotNone(txn)
        self.assertEqual(txn.amount, Decimal("150.00"))

    def test_sales_history_cash_card_vs_end_of_day_calculation(self):
        """
        Verify that SaleListView summary_cash_sales_value (Cash card) correctly computes
        the net physical cash received (sale.total for CASH sales, amount_paid for MIXED sales)
        and matches End-of-Day report logic, rather than inflating by change given.
        """
        from apps.sales.views import SaleListView
        from apps.reports.views import EndOfDayDetailsView

        # 1. CASH sale with change: Total 100, Paid 150, Change 50
        cash_sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("100.00"),
            amount_paid=Decimal("150.00"),
            change_given=Decimal("50.00"),
            payment_method="CASH",
            status="COMPLETED"
        )

        # 2. MIXED sale: Total 200, Paid 70 in cash, 130 on credit
        from apps.customers.models import Customer
        customer = Customer.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            name="Mixed Payment Customer",
            current_balance=Decimal("0.00")
        )
        mixed_sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            customer=customer,
            total=Decimal("200.00"),
            amount_paid=Decimal("70.00"),
            change_given=Decimal("0.00"),
            payment_method="MIXED",
            status="COMPLETED"
        )

        # Build request for SaleListView
        factory = RequestFactory()
        req = factory.get(f"/sales/?shop={self.shop_location.id}")
        req.user = self.admin_user

        view = SaleListView()
        view.setup(req)
        view.object_list = view.get_queryset()
        context = view.get_context_data()

        # Expected physical cash:
        # 100.00 (from full cash sale, net of change) + 70.00 (from mixed cash portion) = 170.00
        # If the bug was present, it would be 150.00 + 70.00 = 220.00 (inflated by change_given of 50.00)
        self.assertEqual(context['summary_cash_sales_value'], Decimal("170.00"))

        # Verify EndOfDayDetailsView calculation for the same sales
        eod_sales = Sale.objects.filter(tenant=self.tenant, shop=self.shop_location, status__in=['COMPLETED', 'PENDING_DISPATCH'])
        eod_cash_full = eod_sales.filter(payment_method='CASH').aggregate(t=Sum('total'))['t'] or Decimal('0.00')
        eod_cash_mixed = eod_sales.filter(payment_method='MIXED').aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
        eod_cash = eod_cash_full + eod_cash_mixed

        self.assertEqual(eod_cash, Decimal("170.00"))
        self.assertEqual(context['summary_cash_sales_value'], eod_cash)

    def test_16_strict_partial_dispatch_halt_walkin_cash_refund(self):
        """
        Verify strict sales workflow halt after partial dispatch for a walk-in cash customer:
        1. Customer buys 10 items @ 10.00 = 100.00 in CASH. Status is PENDING_DISPATCH.
        2. Manager partially dispatches 5 items. Stock ledger records -5 items.
        3. Manager halts further dispatch and requests refund via api_halt_and_request_refund.
        4. Accountant approves refund choosing CASH.
        5. Sale settles to 5 items (total 50.00, amount_paid 50.00, status COMPLETED).
        6. Inventory ledger untouched (no phantom stock added!).
        7. Cashier executes cash payout via api_payout_refund.
        """
        import json
        from apps.sales.models import RefundRequest

        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
        self.tenant.save()

        # Attendant creates and completes cash sale for 10 items
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            payment_method="CASH",
            status="PENDING_DISPATCH"
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("10.00"),
            unit_price=Decimal("10.00"),
            dispatched_quantity=Decimal("0.00")
        )

        # 1. Partial dispatch of 5 units by Shop Manager
        self.client.force_login(self.manager_user)
        disp_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/dispatch/",
            data=json.dumps({'items': [{'sale_item_id': item.pk, 'qty': 5}]}),
            content_type='application/json'
        )
        self.assertEqual(disp_resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.dispatched_quantity, Decimal("5.00"))
        self.assertEqual(item.remaining_quantity, Decimal("5.00"))

        ledger_sales_count = InventoryLedger.objects.filter(
            reference_id=sale.pk, transaction_type="SALE"
        ).count()
        self.assertEqual(ledger_sales_count, 1)

        # 2. Manager halts further dispatch (locks dispatch without deleting items)
        halt_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Customer requested cancellation of remaining 5 units'}),
            content_type='application/json'
        )
        self.assertEqual(halt_resp.status_code, 200)
        data = halt_resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['status'], 'PENDING_DISPATCH')
        self.assertTrue(data['is_halted'])
        self.assertEqual(data['dispatched_total'], "50.00")

        self.assertFalse(RefundRequest.objects.filter(sale=sale).exists())

        # Sale remains in PENDING_DISPATCH with is_halted=True; all line items intact
        sale.refresh_from_db()
        self.assertEqual(sale.status, 'PENDING_DISPATCH')
        self.assertTrue(sale.is_halted)
        self.assertFalse(sale.is_dispatched)
        self.assertEqual(sale.total, Decimal("100.00"))

        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal("10.00"))
        self.assertEqual(item.dispatched_quantity, Decimal("5.00"))
        self.assertEqual(item.remaining_quantity, Decimal("5.00"))

        # Cashier requests refund on halted sale
        self.client.force_login(self.cashier_user)
        ref_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer cancelled remaining 5 units'}),
            content_type='application/json'
        )
        self.assertEqual(ref_resp.status_code, 200)
        refund_req = RefundRequest.objects.get(sale=sale, status='PENDING')

        # Accountant approves -> officially settles the sale to 5 items and COMPLETED
        self.client.force_login(self.accountant_user)
        app_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'CASH'}),
            content_type='application/json'
        )
        self.assertEqual(app_resp.status_code, 200)

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'COMPLETED')
        self.assertTrue(sale.is_dispatched)
        self.assertEqual(sale.total, Decimal("50.00"))

        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal("5.00"))
        self.assertEqual(item.dispatched_quantity, Decimal("5.00"))

        # Crucial check: Inventory ledger entries must NOT change!
        ledger_count_after = InventoryLedger.objects.filter(reference_id=sale.pk).count()
        self.assertEqual(ledger_count_after, 1, "No phantom inventory movements should be generated!")

        # Crucial check: Cashier navbar money must NOT drop upon accountant approval
        ctx_cashier_pending = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_cashier_pending['cash_on_hand'], Decimal("100.00"))

        # 7. Cashier executes cash payout via api_payout_refund
        self.client.force_login(self.cashier_user)
        payout_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({'payout_notes': 'Partial cash refund disbursed'}),
            content_type='application/json'
        )
        self.assertEqual(payout_resp.status_code, 200)

        refund_req.refresh_from_db()
        self.assertEqual(refund_req.payout_status, 'PAID')
        self.assertEqual(refund_req.paid_by, self.cashier_user)

        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, Decimal("50.00"))

        # Cashier navbar money must now drop by the refund amount (100 - 50 = 50)
        ctx_cashier_after = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_cashier_after['cash_on_hand'], Decimal("50.00"))

    def test_17_strict_partial_dispatch_halt_registered_customer_credit(self):
        """
        Verify strict partial dispatch halt for a registered customer on 100% CREDIT:
        1. Customer buys 10 items @ 10.00 = 100.00 on CREDIT. Customer debt increases by 100.00.
        2. 5 items dispatched.
        3. Manager halts dispatch. Refund owed = 0.00, Debt reduction = 50.00.
        4. Accountant approves.
        5. Customer's balance drops from 100.00 to 50.00 (debt reduced by 50.00).
        6. No cashier cash payout is required.
        """
        import json
        from apps.sales.models import RefundRequest

        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
        self.tenant.save()

        customer = Customer.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            name="Credit Customer Partial",
            current_balance=Decimal("0.00")
        )

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            customer=customer,
            total=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            payment_method="CREDIT",
            status="PENDING_DISPATCH"
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("10.00"),
            unit_price=Decimal("10.00"),
            dispatched_quantity=Decimal("0.00")
        )
        # Register debt
        customer.current_balance = Decimal("100.00")
        customer.save()

        # Manager dispatches 5 units
        self.client.force_login(self.manager_user)
        self.client.post(
            f"/sales/api/sales/{sale.pk}/dispatch/",
            data=json.dumps({'items': [{'sale_item_id': item.pk, 'qty': 5}]}),
            content_type='application/json'
        )

        # Manager halts dispatch (locks dispatch)
        halt_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Customer requested halt'}),
            content_type='application/json'
        )
        self.assertEqual(halt_resp.status_code, 200)
        data = halt_resp.json()
        self.assertEqual(data['status'], 'PENDING_DISPATCH')
        self.assertTrue(data['is_halted'])
        self.assertEqual(data['dispatched_total'], "50.00")
        self.assertFalse(RefundRequest.objects.filter(sale=sale).exists())

        # Cashier requests refund / debt reduction on halted sale
        self.client.force_login(self.cashier_user)
        ref_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Cancel undispatched credit items'}),
            content_type='application/json'
        )
        self.assertEqual(ref_resp.status_code, 200)
        refund_req = RefundRequest.objects.get(sale=sale, status='PENDING')

        # Accountant approves -> customer debt reduced by 50.00 and sale settled
        self.client.force_login(self.accountant_user)
        app_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'ACCOUNT_CREDIT'}),
            content_type='application/json'
        )
        self.assertEqual(app_resp.status_code, 200)

        # Verify customer debt reduced upon approval
        customer.refresh_from_db()
        self.assertEqual(customer.current_balance, Decimal("50.00"))

        # Verify debt reduction transaction created
        debt_txn = CustomerTransaction.objects.filter(
            customer=customer, transaction_type="CREDIT", amount=Decimal("50.00")
        ).first()
        self.assertIsNotNone(debt_txn)

        # Sale settled
        sale.refresh_from_db()
        self.assertEqual(sale.status, 'COMPLETED')
        self.assertEqual(sale.total, Decimal("50.00"))

    def test_18_strict_partial_dispatch_halt_registered_customer_account_credit(self):
        """
        Verify strict partial dispatch halt for a registered customer who paid CASH,
        where accountant chooses ACCOUNT_CREDIT as refund method:
        1. Customer pays 100.00 cash for 10 items.
        2. 5 items dispatched.
        3. Manager halts dispatch. Refund owed = 50.00.
        4. Accountant approves with refund_method='ACCOUNT_CREDIT'.
        5. Customer balance becomes -50.00 (store credit). No cashier cash leaves the till.
        """
        import json
        from apps.sales.models import RefundRequest

        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
        self.tenant.save()

        customer = Customer.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            name="Prepaid Registered Customer",
            current_balance=Decimal("0.00")
        )

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            customer=customer,
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            payment_method="CASH",
            status="PENDING_DISPATCH"
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("10.00"),
            unit_price=Decimal("10.00"),
            dispatched_quantity=Decimal("0.00")
        )

        # Dispatch 5 units
        self.client.force_login(self.manager_user)
        self.client.post(
            f"/sales/api/sales/{sale.pk}/dispatch/",
            data=json.dumps({'items': [{'sale_item_id': item.pk, 'qty': 5}]}),
            content_type='application/json'
        )

        # Halt dispatch settles directly to 50.00 (no RefundRequest created)
        halt_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Halt remaining items'}),
            content_type='application/json'
        )
        self.assertEqual(halt_resp.status_code, 200)
        self.assertFalse(RefundRequest.objects.filter(sale=sale).exists())

        # Cashier requests refund on the settled sale
        self.client.force_login(self.cashier_user)
        ref_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Refund to customer store credit account'}),
            content_type='application/json'
        )
        self.assertEqual(ref_resp.status_code, 200)
        refund_req = RefundRequest.objects.get(sale=sale, status='PENDING')

        # Accountant approves with ACCOUNT_CREDIT (immediately fulfilled, no cashier payout needed)
        self.client.force_login(self.accountant_user)
        app_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'ACCOUNT_CREDIT'}),
            content_type='application/json'
        )
        self.assertEqual(app_resp.status_code, 200)

        customer.refresh_from_db()
        self.assertEqual(customer.current_balance, Decimal("-50.00"), "Customer should now have 50.00 store credit!")

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'COMPLETED')
        self.assertEqual(sale.amount_paid, Decimal("50.00"))
        self.assertEqual(sale.total, Decimal("50.00"))

        refund_req.refresh_from_db()
        self.assertEqual(refund_req.payout_status, 'PAID')

    def test_19_registered_customer_credit_payout_cash(self):
        """
        Verify that a registered customer who has positive credit (negative current_balance)
        can withdraw some or all of their credit in cash, with workflow-specific permissions:
        1. In strict workflow: SHOP_CASHIER, ADMIN, ACCOUNTANT can payout; SHOP_MANAGER cannot (403).
        2. In standard workflow: SHOP_MANAGER, ADMIN, ACCOUNTANT can payout; SHOP_CASHIER cannot (403).
        3. Payout decreases customer credit and shift expected_cash.
        4. Over-withdrawal and debt guards are enforced.
        """
        import json

        self.tenant.use_strict_sales_workflow = True
        self.tenant.save()

        # Customer with 100.00 store credit
        customer = Customer.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            name="Credit Holder",
            current_balance=Decimal("-100.00")
        )

        # In strict workflow, SHOP_MANAGER cannot dispense credit payouts
        self.client.force_login(self.manager_user)
        mgr_resp = self.client.post(
            f"/sales/api/customers/{customer.pk}/credit-payout/",
            data=json.dumps({'amount': '20.00'}),
            content_type='application/json'
        )
        self.assertEqual(mgr_resp.status_code, 403)

        # Cashier open shift
        shift = Shift.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.cashier_user,
            opening_cash=Decimal("200.00"),
            status='OPEN'
        )

        # 1. Payout 40.00 by Cashier in strict workflow
        self.client.force_login(self.cashier_user)
        payout_resp = self.client.post(
            f"/sales/api/customers/{customer.pk}/credit-payout/",
            data=json.dumps({'amount': '40.00', 'notes': 'Partial withdrawal'}),
            content_type='application/json'
        )
        self.assertEqual(payout_resp.status_code, 200)
        data = payout_resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['new_balance'], "-60.00")
        self.assertEqual(data['remaining_credit'], "60.00")

        customer.refresh_from_db()
        self.assertEqual(customer.current_balance, Decimal("-60.00"))

        # Verify CustomerTransaction
        txn = CustomerTransaction.objects.get(pk=data['transaction_id'])
        self.assertEqual(txn.transaction_type, 'DEBIT')
        self.assertEqual(txn.amount, Decimal("40.00"))

        # Verify shift expected_cash drops to 160.00
        self.assertEqual(shift.credit_payouts, Decimal("40.00"))
        self.assertEqual(shift.expected_cash, Decimal("160.00"))

        # 2. Over-withdrawal guard
        fail_resp = self.client.post(
            f"/sales/api/customers/{customer.pk}/credit-payout/",
            data=json.dumps({'amount': '70.00'}),
            content_type='application/json'
        )
        self.assertEqual(fail_resp.status_code, 400)
        self.assertIn("exceeds available credit", fail_resp.json()['error'])

        # 3. Customer with debt guard
        debt_customer = Customer.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            name="Debtor",
            current_balance=Decimal("50.00")
        )
        debt_resp = self.client.post(
            f"/sales/api/customers/{debt_customer.pk}/credit-payout/",
            data=json.dumps({'amount': '10.00'}),
            content_type='application/json'
        )
        self.assertEqual(debt_resp.status_code, 400)
        self.assertIn("no available credit", debt_resp.json()['error'])

        # 4. In standard workflow, SHOP_MANAGER can payout, but SHOP_CASHIER cannot
        self.tenant.use_strict_sales_workflow = False
        self.tenant.save()

        self.client.force_login(self.cashier_user)
        cashier_std_resp = self.client.post(
            f"/sales/api/customers/{customer.pk}/credit-payout/",
            data=json.dumps({'amount': '10.00'}),
            content_type='application/json'
        )
        self.assertEqual(cashier_std_resp.status_code, 403)

        self.client.force_login(self.manager_user)
        mgr_std_resp = self.client.post(
            f"/sales/api/customers/{customer.pk}/credit-payout/",
            data=json.dumps({'amount': '10.00', 'notes': 'Manager standard workflow payout'}),
            content_type='application/json'
        )
        self.assertEqual(mgr_std_resp.status_code, 200)
        customer.refresh_from_db()
        self.assertEqual(customer.current_balance, Decimal("-50.00"))

    def test_20_standard_workflow_unaffected(self):
        """
        Verify that the standard sales workflow remains completely unaffected:
        Completing a sale immediately completes and dispatches the sale, deducts inventory,
        and does not require any separate dispatch step.
        """
        self.tenant.use_strict_sales_workflow = False
        self.tenant.save()

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("50.00"),
            payment_method="CASH",
            status="PENDING"
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("5.00"),
            unit_price=Decimal("10.00")
        )

        sale.complete(amount_paid=Decimal("50.00"), payment_method="CASH")
        sale.refresh_from_db()

        self.assertEqual(sale.status, "COMPLETED")
        self.assertTrue(sale.is_dispatched)
        self.assertEqual(sale.amount_paid, Decimal("50.00"))

        # Inventory immediately deducted
        ledger_entry = InventoryLedger.objects.filter(reference_id=sale.pk, transaction_type="SALE").first()
        self.assertIsNotNone(ledger_entry)
        self.assertEqual(ledger_entry.quantity, Decimal("-5.00"))

    def test_21_strict_partial_dispatch_halt_rejection_allows_dispatch_resume(self):
        """
        Verify that rejecting a partial dispatch halt request:
        1. Sets refund request to REJECTED with the rejection reason.
        2. Clears the pending refund lock on the sale.
        3. Allows the shop manager to resume dispatching the remaining items to completion.
        """
        import json
        from apps.sales.models import RefundRequest

        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
        self.tenant.save()

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            payment_method="CASH",
            status="PENDING_DISPATCH"
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("8.00"),
            unit_price=Decimal("10.00"),
            dispatched_quantity=Decimal("0.00")
        )

        # 1. Partial dispatch of 3 units
        self.client.force_login(self.manager_user)
        self.client.post(
            f"/sales/api/sales/{sale.pk}/dispatch/",
            data=json.dumps({'items': [{'sale_item_id': item.pk, 'qty': 3}]}),
            content_type='application/json'
        )
        item.refresh_from_db()
        self.assertEqual(item.dispatched_quantity, Decimal("3.00"))

        # 2. Halt dispatch pauses dispatch without deleting items
        halt_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Customer cancelled remaining items'}),
            content_type='application/json'
        )
        self.assertEqual(halt_resp.status_code, 200)
        self.assertFalse(RefundRequest.objects.filter(sale=sale).exists())

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'PENDING_DISPATCH')
        self.assertTrue(sale.is_halted)

        # 3. Cashier requests refund and Accountant approves -> settles sale to 3 items
        self.client.force_login(self.cashier_user)
        ref_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer cancelled remaining items'}),
            content_type='application/json'
        )
        self.assertEqual(ref_resp.status_code, 200)
        refund_req = RefundRequest.objects.get(sale=sale, status='PENDING')

        self.client.force_login(self.accountant_user)
        app_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'CASH'}),
            content_type='application/json'
        )
        self.assertEqual(app_resp.status_code, 200)

        sale.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(sale.status, 'COMPLETED')
        self.assertEqual(item.dispatched_quantity, Decimal("3.00"))
        self.assertEqual(item.quantity, Decimal("3.00"))
        self.assertTrue(sale.is_dispatched)

    def test_22_strict_partial_dispatch_halt_momo_refund_updates_navbar_balance(self):
        """
        Verify that in strict workflow, when a partial dispatch is halted and settled with a MOMO refund:
        1. Customer buys 10 items @ 10.00 = 100.00 via MOMO.
        2. Manager partially dispatches 4 items (40.00). 6 items undispatched (60.00).
        3. Manager halts dispatch and requests refund of 60.00.
        4. Accountant approves refund with refund_method='MOMO'.
        5. Sale settles to total 40.00, amount_paid 40.00, status COMPLETED.
        6. Cashier navbar momo_balance drops by exactly 60.00 (from 100.00 to 40.00).
        7. No erroneous negative DigitalFundWithdrawal is created that would negate the deduction.
        8. Payout status is immediately PAID (fulfilled via MoMo wallet).
        """
        import json
        from apps.sales.models import RefundRequest
        from apps.accounting.models import DigitalFundWithdrawal

        self.tenant.use_strict_sales_workflow = True
        self.tenant.allow_momo_payments = True
        self.tenant.enable_refunds = True
        self.tenant.save()

        # Attendant creates and completes MOMO sale for 10 items
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            payment_method="MOMO",
            status="PENDING_DISPATCH"
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("10.00"),
            unit_price=Decimal("10.00"),
            dispatched_quantity=Decimal("0.00")
        )

        # Verify initial cashier Momo balance
        ctx_cashier_before = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_cashier_before['momo_balance'], Decimal("100.00"))

        # 1. Partial dispatch of 4 units
        self.client.force_login(self.manager_user)
        disp_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/dispatch/",
            data=json.dumps({'items': [{'sale_item_id': item.pk, 'qty': 4}]}),
            content_type='application/json'
        )
        self.assertEqual(disp_resp.status_code, 200)

        # 2. Manager halts dispatch (settles sale directly to 40.00, no RefundRequest created)
        halt_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Halt remaining items'}),
            content_type='application/json'
        )
        self.assertEqual(halt_resp.status_code, 200)
        self.assertFalse(RefundRequest.objects.filter(sale=sale).exists())

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'PENDING_DISPATCH')
        self.assertTrue(sale.is_halted)

        # 2b. Cashier requests refund on the halted sale (60.00 undispatched)
        self.client.force_login(self.cashier_user)
        ref_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer cancelling remaining items via MoMo'}),
            content_type='application/json'
        )
        self.assertEqual(ref_resp.status_code, 200)
        refund_req = RefundRequest.objects.get(sale=sale, status='PENDING')

        # 3. Accountant approves refund selecting MOMO
        self.client.force_login(self.accountant_user)
        approve_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'MOMO'}),
            content_type='application/json'
        )
        self.assertEqual(approve_resp.status_code, 200)
        refund_req.refresh_from_db()
        self.assertEqual(refund_req.refund_amount, Decimal("60.00"))

        # 4. Verify right after accountant approval: funds are NOT deducted yet
        sale.refresh_from_db()
        self.assertEqual(sale.status, 'COMPLETED')
        self.assertEqual(sale.total, Decimal("40.00"))
        self.assertEqual(sale.amount_paid, Decimal("100.00"))
        ctx_cashier_mid = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_cashier_mid['momo_balance'], Decimal("100.00"), "Approval must not deduct funds!")

        # 5. Verify refund request status right after accountant approval:
        # Payout status remains PENDING until Cashier dispenses payout
        refund_req.refresh_from_db()
        self.assertEqual(refund_req.status, 'APPROVED')
        self.assertEqual(refund_req.refund_method, 'MOMO')
        self.assertEqual(refund_req.payout_status, 'PENDING')
        self.assertEqual(refund_req.reviewed_by, self.accountant_user)
        self.assertIsNone(refund_req.paid_by)

        # 6. Verify Shop Manager CANNOT disburse payout
        self.client.force_login(self.manager_user)
        mgr_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(mgr_resp.status_code, 403)

        # 7. Cashier opens shift and assents/dispenses refund payout
        from apps.sales.models import Shift
        shift = Shift.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.cashier_user,
            opening_cash=Decimal("50.00"),
            status="OPEN"
        )

        self.client.force_login(self.cashier_user)
        payout_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({
                'transaction_reference': 'TXN-MOMO-999',
                'payout_notes': 'Sent by cashier via shop MoMo phone'
            }),
            content_type='application/json'
        )
        self.assertEqual(payout_resp.status_code, 200)

        # 8. Verify sale is settled to 40.00 and stays COMPLETED (customer kept dispatched items)
        sale.refresh_from_db()
        self.assertEqual(sale.status, 'COMPLETED')
        self.assertEqual(sale.amount_paid, Decimal("40.00"))
        self.assertEqual(sale.total, Decimal("40.00"))

        # Verify refund request is now PAID and linked to cashier and shift
        refund_req.refresh_from_db()
        self.assertEqual(refund_req.payout_status, 'PAID')
        self.assertEqual(refund_req.paid_by, self.cashier_user)
        self.assertEqual(refund_req.shift, shift)
        self.assertIn('Ref: TXN-MOMO-999', refund_req.payout_notes)
        self.assertIn('Sent by cashier via shop MoMo phone', refund_req.payout_notes)

        # Verify shift accounts for the momo refund payout (60.00)
        self.assertEqual(shift.momo_refund_payouts, Decimal("60.00"))
        self.assertEqual(shift.refund_payouts, Decimal("0.00"))

        # 7. Verify NO negative DigitalFundWithdrawal was created that would cancel out deduction
        buggy_dfw = DigitalFundWithdrawal.objects.filter(
            shop=self.shop_location,
            fund_source='MOMO',
            notes__icontains=refund_req.refund_number
        ).first()
        self.assertIsNone(buggy_dfw, "No DigitalFundWithdrawal should be created for same-method MOMO refunds")

        # 8. CRITICAL: Cashier navbar momo_balance must be exactly 40.00 (100.00 - 60.00)
        ctx_cashier_after = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_cashier_after['momo_balance'], Decimal("40.00"))

        # Also verify Shop Manager navbar momo_balance is 40.00
        ctx_manager_after = self.get_navbar_context(self.manager_user)
        self.assertEqual(ctx_manager_after['momo_balance'], Decimal("40.00"))

    def test_23_cashier_cash_refund_payout_deducts_shift_expected_cash(self):
        """
        Verify that when a Cash refund is disbursed by a cashier:
        1. Accountant approves refund (status APPROVED, payout_status PENDING).
        2. Cashier dispenses cash refund with active shift.
        3. Shift.refund_payouts increases by the refund amount.
        4. Shift.expected_cash is reduced so cashier cash count is balanced.
        5. Cashier navbar cash_on_hand is accurately reduced.
        """
        import json
        from apps.sales.models import Shift, RefundRequest

        # Cashier starts shift with 100 opening cash
        shift = Shift.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.cashier_user,
            opening_cash=Decimal("100.00"),
            status="OPEN"
        )

        # Cash sale of 100 made during shift
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.cashier_user,
            cashier=self.cashier_user,
            shift=shift,
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            payment_method="CASH",
            status="COMPLETED"
        )

        # Shift expected cash is 100 opening + 100 cash sales = 200
        self.assertEqual(shift.expected_cash, Decimal("200.00"))

        # Create refund request for 30.00 cash
        refund_req = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale,
            refund_method='CASH',
            refund_amount=Decimal("30.00"),
            reason='Customer returned 1 item',
            requested_by=self.manager_user,
            status='PENDING',
            payout_status='PENDING'
        )

        # Accountant approves refund
        self.client.force_login(self.accountant_user)
        approve_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'CASH'}),
            content_type='application/json'
        )
        self.assertEqual(approve_resp.status_code, 200)

        refund_req.refresh_from_db()
        self.assertEqual(refund_req.status, 'APPROVED')
        self.assertEqual(refund_req.payout_status, 'PENDING')

        # Verify that upon accountant approval, cash_on_hand and shift.expected_cash do NOT prematurely drop
        self.assertEqual(shift.expected_cash, Decimal("200.00"))
        ctx_pending = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx_pending['cash_on_hand'], Decimal("200.00"))

        # Cashier assents and dispenses the cash refund
        self.client.force_login(self.cashier_user)
        payout_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({
                'payout_notes': 'Paid in cash from drawer'
            }),
            content_type='application/json'
        )
        self.assertEqual(payout_resp.status_code, 200)

        refund_req.refresh_from_db()
        self.assertEqual(refund_req.payout_status, 'PAID')
        self.assertEqual(refund_req.paid_by, self.cashier_user)
        self.assertEqual(refund_req.shift, shift)

        # Shift expected cash must now be 170.00 (200 - 30)
        self.assertEqual(shift.refund_payouts, Decimal("30.00"))
        self.assertEqual(shift.expected_cash, Decimal("170.00"))

        # Cashier's cash_on_hand context must now accurately deduct the refund payout
        ctx = self.get_navbar_context(self.cashier_user)
        self.assertEqual(ctx['cash_on_hand'], Decimal("170.00"))  # 100 opening + 100 sale - 30 refund

    def test_21_refund_button_hidden_while_awaiting_payout(self):
        """
        Verify that after an accountant approves a refund request:
        1. sale.has_approved_pending_payout is True while awaiting cashier disbursement.
        2. /sales/api/<id>/detail/ returns has_approved_pending_payout=True and approved_refund_info.
        3. Attempting to request another refund via api_refund_sale returns 400 error.
        4. Sale list view annotates _has_approved_pending_payout=True and filters by pending_payout.
        5. Once cashier dispenses payout, has_approved_pending_payout resets to False and sale is REFUNDED.
        """
        import json
        from apps.sales.models import RefundRequest

        self.tenant.enable_refunds = True
        self.tenant.save()

        # 1. Attendant creates completed sale
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("120.00"),
            amount_paid=Decimal("120.00"),
            payment_method="CASH",
            status="COMPLETED"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("4.00"),
            unit_price=Decimal("30.00"),
            dispatched_quantity=Decimal("4.00")
        )

        # Initially no refund pending
        self.assertFalse(sale.has_pending_refund)
        self.assertFalse(sale.has_approved_pending_payout)

        # 2. Manager requests refund
        self.client.force_login(self.manager_user)
        req_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Defective items'}),
            content_type='application/json'
        )
        self.assertEqual(req_resp.status_code, 200)

        sale.refresh_from_db()
        self.assertTrue(sale.has_pending_refund)
        self.assertFalse(sale.has_approved_pending_payout)

        refund_req = RefundRequest.objects.get(sale=sale)
        self.assertEqual(refund_req.status, 'PENDING')
        self.assertEqual(refund_req.payout_status, 'PENDING')

        # 3. Accountant approves refund
        self.client.force_login(self.accountant_user)
        appr_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'CASH'}),
            content_type='application/json'
        )
        self.assertEqual(appr_resp.status_code, 200)

        sale.refresh_from_db()
        self.assertFalse(sale.has_pending_refund)
        self.assertTrue(sale.has_approved_pending_payout)
        self.assertEqual(sale.status, 'COMPLETED')  # Not yet refunded until cashier disburses cash

        # 4. Detail API check
        detail_resp = self.client.get(f"/sales/api/{sale.pk}/detail/")
        self.assertEqual(detail_resp.status_code, 200)
        detail_data = detail_resp.json()
        self.assertFalse(detail_data['has_pending_refund'])
        self.assertTrue(detail_data['has_approved_pending_payout'])
        self.assertIsNotNone(detail_data['approved_refund_info'])
        self.assertEqual(detail_data['approved_refund_info']['refund_number'], refund_req.refund_number)

        # 5. Duplicate refund request must be rejected
        self.client.force_login(self.manager_user)
        dup_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Try again'}),
            content_type='application/json'
        )
        self.assertEqual(dup_resp.status_code, 400)
        self.assertIn('awaiting cashier disbursement', dup_resp.json()['error'])

        # 6. Sales list view check
        list_resp = self.client.get("/sales/?refund_status=pending_payout")
        self.assertEqual(list_resp.status_code, 200)
        sales_in_list = list(list_resp.context['sales'])
        self.assertTrue(any(s.pk == sale.pk for s in sales_in_list))

        # 7. Cashier executes payout
        self.client.force_login(self.cashier_user)
        payout_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({'payout_notes': 'Paid cash'}),
            content_type='application/json'
        )
        self.assertEqual(payout_resp.status_code, 200)

        sale.refresh_from_db()
        self.assertFalse(sale.has_approved_pending_payout)
        self.assertEqual(sale.status, 'REFUNDED')

    def test_24_accountant_and_admin_can_cancel_approved_undisbursed_refund(self):
        """
        Verify that an accountant and admin can cancel an approved refund before disbursement:
        1. A completed sale has a refund request submitted by shop manager.
        2. Accountant approves the refund request (status='APPROVED', payout_status='PENDING').
        3. Sale has sale.has_approved_pending_payout == True.
        4. Attendant/Cashier cannot cancel the approval (403 Forbidden).
        5. Accountant cancels the approval via /sales/api/refund-requests/<id>/cancel/ with reason.
        6. RefundRequest transitions to status='CANCELLED' with cancellation reason and reviewed_by=accountant.
        7. Sale has sale.has_approved_pending_payout == False, and remains COMPLETED.
        8. Cashier cannot disburse payout on the cancelled refund request (returns 400 error).
        9. Admin can also cancel an approved refund request.
        10. A refund that has already been paid out (payout_status='PAID') cannot be cancelled (returns 400 error).
        """
        import json
        from apps.sales.models import RefundRequest

        self.tenant.enable_refunds = True
        self.tenant.save()

        # 1. Attendant creates completed sale
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            payment_method="CASH",
            status="COMPLETED"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("2.00"),
            unit_price=Decimal("40.00"),
            dispatched_quantity=Decimal("2.00")
        )

        # 2. Manager requests refund
        self.client.force_login(self.manager_user)
        req_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer requested refund'}),
            content_type='application/json'
        )
        self.assertEqual(req_resp.status_code, 200)

        refund_req = RefundRequest.objects.get(sale=sale)
        self.assertEqual(refund_req.status, 'PENDING')

        # 3. Accountant approves refund
        self.client.force_login(self.accountant_user)
        appr_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'CASH'}),
            content_type='application/json'
        )
        self.assertEqual(appr_resp.status_code, 200)

        sale.refresh_from_db()
        refund_req.refresh_from_db()
        self.assertEqual(refund_req.status, 'APPROVED')
        self.assertEqual(refund_req.payout_status, 'PENDING')
        self.assertTrue(sale.has_approved_pending_payout)

        # 4. Attendant / Cashier attempts to cancel approval -> 403 Forbidden
        self.client.force_login(self.cashier_user)
        cashier_cancel_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/cancel/",
            data=json.dumps({'reason': 'Cashier trying to cancel'}),
            content_type='application/json'
        )
        self.assertEqual(cashier_cancel_resp.status_code, 403)

        # 5. Accountant cancels approval
        self.client.force_login(self.accountant_user)
        cancel_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/cancel/",
            data=json.dumps({'reason': 'Customer changed mind and kept items'}),
            content_type='application/json'
        )
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertTrue(cancel_resp.json()['success'])

        # 6. Verify RefundRequest state
        refund_req.refresh_from_db()
        self.assertEqual(refund_req.status, 'CANCELLED')
        self.assertEqual(refund_req.reviewed_by, self.accountant_user)
        self.assertIn('Customer changed mind', refund_req.rejection_reason)

        # 7. Verify Sale state is unblocked
        sale.refresh_from_db()
        self.assertFalse(sale.has_approved_pending_payout)
        self.assertFalse(sale.has_pending_refund)
        self.assertEqual(sale.status, 'COMPLETED')

        # 8. Cashier cannot disburse payout on cancelled refund
        self.client.force_login(self.cashier_user)
        payout_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({'payout_notes': 'Trying to disburse cancelled'}),
            content_type='application/json'
        )
        self.assertEqual(payout_resp.status_code, 400)
        self.assertIn('must be approved before payout', payout_resp.json()['error'])

        # 9. Verify Admin can also cancel an approved refund
        # Submit a new refund request for the same sale now that the previous was cancelled
        self.client.force_login(self.manager_user)
        req2_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Second refund request'}),
            content_type='application/json'
        )
        self.assertEqual(req2_resp.status_code, 200)

        refund_req2 = RefundRequest.objects.filter(sale=sale, status='PENDING').first()
        self.assertIsNotNone(refund_req2)

        # Approve by accountant
        self.client.force_login(self.accountant_user)
        self.client.post(
            f"/sales/api/refund-requests/{refund_req2.pk}/approve/",
            data=json.dumps({'refund_method': 'CASH'}),
            content_type='application/json'
        )
        refund_req2.refresh_from_db()
        self.assertEqual(refund_req2.status, 'APPROVED')

        # Admin cancels approval
        self.client.force_login(self.admin_user)
        admin_cancel_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req2.pk}/cancel/",
            data=json.dumps({'reason': 'Admin decided to cancel'}),
            content_type='application/json'
        )
        self.assertEqual(admin_cancel_resp.status_code, 200)
        refund_req2.refresh_from_db()
        self.assertEqual(refund_req2.status, 'CANCELLED')
        self.assertEqual(refund_req2.reviewed_by, self.admin_user)

        # 10. Cannot cancel a refund that has already been disbursed
        sale3 = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("50.00"),
            amount_paid=Decimal("50.00"),
            payment_method="CASH",
            status="COMPLETED"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale3,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.00"),
            unit_price=Decimal("50.00"),
            dispatched_quantity=Decimal("1.00")
        )
        refund_req3 = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale3,
            requested_by=self.manager_user,
            reason='Paid refund',
            status='PENDING',
            refund_amount=Decimal('50.00'),
            refund_method='CASH'
        )
        refund_req3.approve(self.accountant_user, refund_method='CASH')
        refund_req3.execute_payout(self.cashier_user)
        refund_req3.refresh_from_db()
        self.assertEqual(refund_req3.payout_status, 'PAID')

        # Attempt to cancel paid refund -> 400
        self.client.force_login(self.accountant_user)
        paid_cancel_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req3.pk}/cancel/",
            data=json.dumps({'reason': 'Cancel paid'}),
            content_type='application/json'
        )
        self.assertEqual(paid_cancel_resp.status_code, 400)
        self.assertIn('already been paid out', paid_cancel_resp.json()['error'])

        # 11. Verify that if a sale was marked REFUNDED prematurely before payout,
        # cancelling approval restores sale.status to COMPLETED and allows cashier to re-request refund
        sale4 = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("60.00"),
            amount_paid=Decimal("60.00"),
            payment_method="CASH",
            status="REFUNDED",  # Prematurely refunded
            notes="REFUNDED: Testing refund reversal"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale4,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.00"),
            unit_price=Decimal("60.00"),
            dispatched_quantity=Decimal("1.00")
        )
        refund_req4 = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale4,
            requested_by=self.cashier_user,
            reason='Premature refund test',
            status='APPROVED',
            payout_status='PENDING',
            refund_amount=Decimal('60.00'),
            refund_method='CASH'
        )
        # Cancel approval by accountant
        self.client.force_login(self.accountant_user)
        cancel4_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req4.pk}/cancel/",
            data=json.dumps({'reason': 'Cancelling premature refund'}),
            content_type='application/json'
        )
        self.assertEqual(cancel4_resp.status_code, 200)

        sale4.refresh_from_db()
        self.assertEqual(sale4.status, 'COMPLETED')
        self.assertFalse(sale4.has_approved_pending_payout)
        self.assertFalse(sale4.has_pending_refund)

        # Cashier can now request refund again
        self.client.force_login(self.cashier_user)
        rerequest_resp = self.client.post(
            f"/sales/api/{sale4.pk}/refund/",
            data=json.dumps({'reason': 'Customer returned with receipt again'}),
            content_type='application/json'
        )
        self.assertEqual(rerequest_resp.status_code, 200)
        self.assertTrue(rerequest_resp.json()['success'])

        # 12. Test reset_refunded_sale management command
        from django.core.management import call_command
        sale5 = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("95.00"),
            amount_paid=Decimal("95.00"),
            payment_method="CASH",
            status="REFUNDED",
            notes="REFUNDED: Orphaned refund status"
        )
        call_command('reset_refunded_sale', sale_id=sale5.pk)
        sale5.refresh_from_db()
        self.assertEqual(sale5.status, 'COMPLETED')

    def test_25_disbursement_payout_with_refund_always_cash_false(self):
        """
        Test that when tenant.refund_always_cash = False:
        1. MOMO sales approved as MOMO can be disbursed by cashier and admin without error.
        2. ECASH sales approved as ECASH can be disbursed and update the ECashLedger.
        3. Payout execution is idempotent even if sale.status was already REFUNDED.
        4. Context processor returns correct momo and ecash balances for ADMIN role.
        """
        import json
        from apps.sales.models import RefundRequest
        self.tenant.enable_refunds = True
        self.tenant.refund_always_cash = False
        self.tenant.save()

        # 1. MOMO Sale Refund & Payout
        momo_sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("120.00"),
            amount_paid=Decimal("120.00"),
            payment_method="MOMO",
            status="COMPLETED"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=momo_sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("2.00"),
            unit_price=Decimal("60.00"),
            dispatched_quantity=Decimal("2.00")
        )

        # Shop Manager requests refund
        self.client.force_login(self.manager_user)
        req_resp = self.client.post(
            f"/sales/api/{momo_sale.pk}/refund/",
            data=json.dumps({'reason': 'MOMO refund requested'}),
            content_type='application/json'
        )
        self.assertEqual(req_resp.status_code, 200)
        rr_momo = RefundRequest.objects.get(sale=momo_sale)

        # Accountant approves with method MOMO
        self.client.force_login(self.accountant_user)
        app_resp = self.client.post(
            f"/sales/api/refund-requests/{rr_momo.pk}/approve/",
            data=json.dumps({'refund_method': 'MOMO'}),
            content_type='application/json'
        )
        self.assertEqual(app_resp.status_code, 200)
        rr_momo.refresh_from_db()
        self.assertEqual(rr_momo.status, 'APPROVED')
        self.assertEqual(rr_momo.payout_status, 'PENDING')
        self.assertEqual(rr_momo.refund_method, 'MOMO')

        # Cashier disburses MOMO refund
        self.client.force_login(self.cashier_user)
        payout_resp = self.client.post(
            f"/sales/api/refund-requests/{rr_momo.pk}/payout/",
            data=json.dumps({'transaction_reference': 'MOMO-TX-9988', 'payout_notes': 'Sent to customer wallet'}),
            content_type='application/json'
        )
        self.assertEqual(payout_resp.status_code, 200)
        self.assertTrue(payout_resp.json()['success'])

        rr_momo.refresh_from_db()
        momo_sale.refresh_from_db()
        self.assertEqual(rr_momo.payout_status, 'PAID')
        self.assertEqual(rr_momo.paid_by, self.cashier_user)
        self.assertEqual(momo_sale.status, 'REFUNDED')

        # 2. ECASH Sale Refund & Payout
        ecash_sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            payment_method="ECASH",
            status="COMPLETED"
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=ecash_sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("1.00"),
            unit_price=Decimal("80.00"),
            dispatched_quantity=Decimal("1.00")
        )

        rr_ecash = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=ecash_sale,
            requested_by=self.cashier_user,
            reason='ECash refund test',
            status='PENDING'
        )
        rr_ecash.approve(self.accountant_user, refund_method='ECASH')
        rr_ecash.refresh_from_db()
        self.assertEqual(rr_ecash.refund_method, 'ECASH')

        # Admin disburses ECash refund
        self.client.force_login(self.admin_user)
        ecash_payout_resp = self.client.post(
            f"/sales/api/refund-requests/{rr_ecash.pk}/payout/",
            data=json.dumps({'transaction_reference': 'PAYSTACK-REF-123'}),
            content_type='application/json'
        )
        self.assertEqual(ecash_payout_resp.status_code, 200)
        self.assertTrue(ecash_payout_resp.json()['success'])

        rr_ecash.refresh_from_db()
        ecash_sale.refresh_from_db()
        self.assertEqual(rr_ecash.payout_status, 'PAID')
        self.assertEqual(ecash_sale.status, 'REFUNDED')

        from apps.payments.models import ECashLedger
        self.assertTrue(ECashLedger.objects.filter(reference_type='RefundRequest', reference_id=rr_ecash.pk).exists())

        # 3. Idempotent payout execution if sale was already marked REFUNDED
        sale_already_refunded = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("50.00"),
            amount_paid=Decimal("50.00"),
            payment_method="CASH",
            status="REFUNDED"
        )
        rr_already_refunded = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale_already_refunded,
            requested_by=self.cashier_user,
            reason='Already refunded sale test',
            status='APPROVED',
            payout_status='PENDING',
            refund_amount=Decimal('50.00'),
            refund_method='CASH'
        )
        payout_already_resp = self.client.post(
            f"/sales/api/refund-requests/{rr_already_refunded.pk}/payout/",
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(payout_already_resp.status_code, 200)
        rr_already_refunded.refresh_from_db()
        self.assertEqual(rr_already_refunded.payout_status, 'PAID')

        # 4. Context processor for ADMIN includes digital balances without error
        from apps.core.context_processors import tenant_context
        from django.test import RequestFactory
        factory = RequestFactory()
        req = factory.get('/sales/refund-requests/')
        req.user = self.admin_user
        ctx = tenant_context(req)
        self.assertIn('momo_balance', ctx)
        self.assertIn('ecash_balance', ctx)

    def test_29_strict_mode_partial_dispatch_refund_block_and_cashier_only_permissions(self):
        """
        Verify strict mode rules:
        1. Partially dispatched sale blocks cashier from requesting refund until Shop Manager runs Halt & Settle.
        2. Halt & Settle directly settles the sale to dispatched quantities with NO RefundRequest created.
        3. Once settled, cashier can request a refund.
        4. In strict mode, only SHOP_CASHIER can request refunds (MANAGER, ACCOUNTANT, ADMIN get 403).
        5. In strict mode, only SHOP_CASHIER can disburse refund payouts (ADMIN gets 403).
        """
        import json
        from apps.sales.models import RefundRequest

        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
        self.tenant.save()

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            payment_method="CASH",
            status="PENDING_DISPATCH"
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("10.00"),
            unit_price=Decimal("10.00"),
            dispatched_quantity=Decimal("0.00")
        )

        # 0. Zero items dispatched: Cashier is already BLOCKED because sale is PENDING_DISPATCH
        self.client.force_login(self.cashier_user)
        zero_disp_blocked = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer wants refund before dispatch'}),
            content_type='application/json'
        )
        self.assertEqual(zero_disp_blocked.status_code, 400)
        self.assertIn("must halt dispatch", zero_disp_blocked.json()['error'].lower())

        # 1. Manager partially dispatches 4 of 10 units
        self.client.force_login(self.manager_user)
        disp_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/dispatch/",
            data=json.dumps({'items': [{'sale_item_id': item.pk, 'qty': 4}]}),
            content_type='application/json'
        )
        self.assertEqual(disp_resp.status_code, 200)
        sale.refresh_from_db()
        self.assertTrue(sale.is_partially_dispatched)

        # 2. Cashier is STILL BLOCKED from requesting refund while pending dispatch
        self.client.force_login(self.cashier_user)
        blocked_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer wants refund'}),
            content_type='application/json'
        )
        self.assertEqual(blocked_resp.status_code, 400)
        self.assertIn("must halt dispatch", blocked_resp.json()['error'].lower())

        # 3. Non-cashier roles are forbidden from requesting refunds in strict mode
        for non_cashier in [self.manager_user, self.accountant_user, self.admin_user]:
            self.client.force_login(non_cashier)
            resp = self.client.post(
                f"/sales/api/{sale.pk}/refund/",
                data=json.dumps({'reason': 'Unauthorized role'}),
                content_type='application/json'
            )
            self.assertEqual(resp.status_code, 403)

        # 4. Manager halts dispatch -> locks dispatch and allows cashier to request refund
        self.client.force_login(self.manager_user)
        halt_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Customer took 4 units only'}),
            content_type='application/json'
        )
        self.assertEqual(halt_resp.status_code, 200)
        self.assertFalse(RefundRequest.objects.filter(sale=sale).exists())

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'PENDING_DISPATCH')
        self.assertTrue(sale.is_halted)
        self.assertEqual(sale.total, Decimal("100.00"))

        # 5. Now Cashier can request a refund on the halted sale (refunds excess paid: 100 - 40 = 60)
        self.client.force_login(self.cashier_user)
        req_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Refunding halted sale'}),
            content_type='application/json'
        )
        self.assertEqual(req_resp.status_code, 200)
        refund_req = RefundRequest.objects.get(sale=sale, status='PENDING')
        self.assertTrue(refund_req.is_partial_halt)
        self.assertEqual(refund_req.refund_amount, Decimal("60.00"))

        # 6. Accountant approves -> settles sale to 40.00 and COMPLETED
        self.client.force_login(self.accountant_user)
        app_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'CASH'}),
            content_type='application/json'
        )
        self.assertEqual(app_resp.status_code, 200)

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'COMPLETED')
        self.assertEqual(sale.total, Decimal("40.00"))

        # 7. Admin CANNOT disburse payout in strict mode (only SHOP_CASHIER)
        self.client.force_login(self.admin_user)
        admin_payout = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(admin_payout.status_code, 403)

        # 8. Cashier CAN disburse payout
        self.client.force_login(self.cashier_user)
        cashier_payout = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(cashier_payout.status_code, 200)
        refund_req.refresh_from_db()
        self.assertEqual(refund_req.payout_status, 'PAID')

        # Sale amount_paid is adjusted to match total of kept goods (40.00)
        sale.refresh_from_db()
        self.assertEqual(sale.amount_paid, Decimal("40.00"))
        self.assertEqual(sale.total, Decimal("40.00"))
        self.assertEqual(sale.status, 'COMPLETED')

    def test_30_zero_dispatch_halt_allows_full_refund_without_phantom_inventory(self):
        """
        Verify that when a sale in strict mode has 0 items dispatched:
        1. Cashier cannot request refund while sale is in PENDING_DISPATCH.
        2. Shop Manager halts dispatch (zero items dispatched).
        3. All original sale items remain intact with original line totals.
        4. Sale is COMPLETED with is_dispatched=True.
        5. Cashier requests a 100% refund.
        6. Accountant approves and Cashier disburses payout.
        7. Sale is marked REFUNDED and NO phantom InventoryLedger SALE_RETURN entries are created.
        """
        import json
        from apps.sales.models import RefundRequest
        from apps.inventory.models import InventoryLedger

        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
        self.tenant.save()

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            total=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            payment_method="CASH",
            status="PENDING_DISPATCH"
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("8.00"),
            unit_price=Decimal("10.00"),
            dispatched_quantity=Decimal("0.00")
        )

        initial_ledger_count = InventoryLedger.objects.filter(reference_id=sale.pk).count()

        # 1. Cashier is blocked from refunding while in PENDING_DISPATCH
        self.client.force_login(self.cashier_user)
        ref_block = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer changed mind'}),
            content_type='application/json'
        )
        self.assertEqual(ref_block.status_code, 400)
        self.assertIn("must halt dispatch", ref_block.json()['error'].lower())

        # 2. Shop Manager halts dispatch before any items were released
        self.client.force_login(self.manager_user)
        halt_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Customer cancelled before collection'}),
            content_type='application/json'
        )
        self.assertEqual(halt_resp.status_code, 200)

        sale.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(sale.status, 'PENDING_DISPATCH')
        self.assertTrue(sale.is_halted)
        self.assertFalse(sale.is_dispatched)
        # All items and full total preserved
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(item.quantity, Decimal("8.00"))
        self.assertEqual(sale.total, Decimal("80.00"))
        self.assertIn("Dispatch Halted", sale.notes)

        # 3. Cashier can now request 100% refund
        self.client.force_login(self.cashier_user)
        req_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Full refund cancelled order'}),
            content_type='application/json'
        )
        self.assertEqual(req_resp.status_code, 200)

        refund_req = RefundRequest.objects.get(sale=sale, status='PENDING')

        # 4. Accountant approves
        self.client.force_login(self.accountant_user)
        app_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/approve/",
            data=json.dumps({'refund_method': 'CASH'}),
            content_type='application/json'
        )
        self.assertEqual(app_resp.status_code, 200)

        # 5. Cashier disburses payout
        self.client.force_login(self.cashier_user)
        payout_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/payout/",
            data=json.dumps({}),
            content_type='application/json'
        )
        self.assertEqual(payout_resp.status_code, 200)

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'REFUNDED')

        # 6. Verify NO phantom inventory entries were created
        final_ledger_count = InventoryLedger.objects.filter(reference_id=sale.pk).count()
        self.assertEqual(final_ledger_count, initial_ledger_count, "No inventory should be returned when zero items were dispatched")

    def test_32_cashier_can_resume_halted_transaction(self):
        """
        Verify that when a shop manager halts a sale by mistake in strict sales workflow,
        the cashier can resume the transaction.
        The sale returns to PENDING_DISPATCH, is_dispatched=False, and all original items
        and quantities are completely restored so dispatch can continue.
        """
        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
        self.tenant.save()
        initial_ledger_count = InventoryLedger.objects.count()

        # 1. Create a sale with 10 units @ 10 GHS = 100 GHS
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            payment_method='CASH',
            status='PENDING_DISPATCH',
            is_dispatched=False
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("10.00"),
            unit_price=Decimal("10.00"),
            total=Decimal("100.00")
        )

        # 2. Partially dispatch 4 items
        self.client.force_login(self.manager_user)
        disp_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/dispatch/",
            data=json.dumps({'items': [{'sale_item_id': item.pk, 'qty': '4.00'}]}),
            content_type='application/json'
        )
        self.assertEqual(disp_resp.status_code, 200)

        # 3. Shop Manager halts and settles by mistake
        halt_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Halted by mistake'}),
            content_type='application/json'
        )
        self.assertEqual(halt_resp.status_code, 200)

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'PENDING_DISPATCH')
        self.assertFalse(sale.is_dispatched)
        self.assertTrue(sale.is_halted)
        self.assertTrue(sale.can_be_resumed)
        self.assertEqual(sale.total, Decimal("100.00"))
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(sale.items.first().quantity, Decimal("10.00"))
        self.assertEqual(sale.items.first().dispatched_quantity, Decimal("4.00"))
        self.assertEqual(sale.items.first().remaining_quantity, Decimal("6.00"))

        # 4. Check api_sale_detail returns resumption metadata
        self.client.force_login(self.cashier_user)
        detail_resp = self.client.get(f"/sales/api/{sale.pk}/detail/")
        self.assertEqual(detail_resp.status_code, 200)
        detail_data = detail_resp.json()
        self.assertTrue(detail_data['can_be_resumed'])
        self.assertTrue(detail_data['is_halted'])
        self.assertEqual(detail_data['calculated_refund_amount'], "60.00")

        # 5. Cashier resumes the transaction
        resume_resp = self.client.post(
            f"/sales/api/sales/{sale.pk}/resume-dispatch/",
            data=json.dumps({'reason': 'Shop manager clicked halt by mistake'}),
            content_type='application/json'
        )
        self.assertEqual(resume_resp.status_code, 200)
        self.assertIn("resumed", resume_resp.json()['message'].lower())

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'PENDING_DISPATCH')
        self.assertFalse(sale.is_dispatched)
        self.assertFalse(sale.is_halted)
        self.assertFalse(sale.can_be_resumed)
        self.assertEqual(sale.total, Decimal("100.00"))
        self.assertEqual(sale.amount_paid, Decimal("100.00"))
        self.assertIn("Dispatch Resumed", sale.notes)

        # Verify items and quantities are fully restored
        self.assertEqual(sale.items.count(), 1)
        restored_item = sale.items.first()
        self.assertEqual(restored_item.quantity, Decimal("10.00"))
        self.assertEqual(restored_item.dispatched_quantity, Decimal("4.00"))
        self.assertEqual(restored_item.remaining_quantity, Decimal("6.00"))

        # 6. Shop Manager now successfully dispatches the remaining 6 items
        self.client.force_login(self.manager_user)
        disp_resp2 = self.client.post(
            f"/sales/api/sales/{sale.pk}/dispatch/",
            data=json.dumps({'items': [{'sale_item_id': restored_item.pk, 'qty': '6.00'}]}),
            content_type='application/json'
        )
        self.assertEqual(disp_resp2.status_code, 200)

        sale.refresh_from_db()
        self.assertEqual(sale.status, 'COMPLETED')
        self.assertTrue(sale.is_dispatched)
        self.assertTrue(sale.all_items_dispatched)

    def test_33_resume_blocked_when_refund_pending_or_approved(self):
        """
        Verify that a halted sale cannot be resumed if a refund request is pending
        approval (must be cancelled first) or already approved/disbursed.
        """
        self.tenant.use_strict_sales_workflow = True
        self.tenant.enable_refunds = True
        self.tenant.save()

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            subtotal=Decimal("80.00"),
            total=Decimal("80.00"),
            amount_paid=Decimal("80.00"),
            payment_method='CASH',
            status='PENDING_DISPATCH',
            is_dispatched=False
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            batch=self.shop_batch,
            quantity=Decimal("8.00"),
            unit_price=Decimal("10.00"),
            total=Decimal("80.00")
        )

        # Halt sale
        self.client.force_login(self.manager_user)
        self.client.post(
            f"/sales/api/sales/{sale.pk}/halt-dispatch/",
            data=json.dumps({'reason': 'Halted'}),
            content_type='application/json'
        )

        # Cashier requests refund -> status=PENDING
        self.client.force_login(self.cashier_user)
        self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer requested refund'}),
            content_type='application/json'
        )

        sale.refresh_from_db()
        self.assertFalse(sale.can_be_resumed)

        # Resuming while refund request is pending must be rejected
        res_fail = self.client.post(
            f"/sales/api/sales/{sale.pk}/resume-dispatch/",
            data=json.dumps({'reason': 'Try resume'}),
            content_type='application/json'
        )
        self.assertEqual(res_fail.status_code, 400)
        self.assertIn("pending approval", res_fail.json()['error'].lower())

        # If accountant rejects the pending refund request
        refund_req = RefundRequest.objects.get(sale=sale, status='PENDING')
        self.client.force_login(self.accountant_user)
        reject_resp = self.client.post(
            f"/sales/api/refund-requests/{refund_req.pk}/reject/",
            data=json.dumps({'reason': 'Rejected so cashier can resume'}),
            content_type='application/json'
        )
        self.assertEqual(reject_resp.status_code, 200)

        # Now cashier can resume!
        sale.refresh_from_db()
        self.assertTrue(sale.can_be_resumed)
        self.client.force_login(self.cashier_user)
        res_ok = self.client.post(
            f"/sales/api/sales/{sale.pk}/resume-dispatch/",
            data=json.dumps({'reason': 'Resuming after rejected refund'}),
            content_type='application/json'
        )
        self.assertEqual(res_ok.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.status, 'PENDING_DISPATCH')

    def test_34_legacy_refund_request_effective_dispatched_total(self):
        """
        Verify that legacy or pre-existing refund requests where dispatched_total is 0.00
        correctly report their effective dispatched goods value via effective_dispatched_total
        and api_sale_detail.
        """
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            cashier=self.cashier_user,
            subtotal=Decimal("40.00"),
            total=Decimal("40.00"),
            amount_paid=Decimal("100.00"),
            payment_method='CASH',
            status='COMPLETED',
            is_dispatched=True
        )

        # Create a legacy RefundRequest directly with dispatched_total=0
        refund_req = RefundRequest.objects.create(
            tenant=self.tenant,
            sale=sale,
            requested_by=self.cashier_user,
            refund_amount=Decimal("60.00"),
            dispatched_total=Decimal("0.00"),
            is_partial_halt=True,
            reason="Legacy halted refund"
        )
        RefundRequest.objects.filter(pk=refund_req.pk).update(dispatched_total=Decimal("0.00"))
        refund_req.refresh_from_db()
        self.assertEqual(refund_req.dispatched_total, Decimal("0.00"))

        # Verify effective_dispatched_total resolves to 40.00
        self.assertEqual(refund_req.effective_dispatched_total, Decimal("40.00"))

        # Verify api_sale_detail returns 40.00 for dispatched_total
        self.client.force_login(self.cashier_user)
        resp = self.client.get(f"/sales/api/{sale.pk}/detail/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['refunds'][0]['dispatched_total'], "40.00")

    def test_35_partial_dispatch_halt_refund_amount_calculation(self):
        """
        Verify that when a sale is partially dispatched and halted by the manager:
        1. sale.dispatched_total accurately reflects the dispatched items (e.g. 140.00).
        2. sale.undispatched_total and sale.calculated_refund_amount reflect the undispatched items (e.g. 155.00).
        3. api_sale_detail returns dispatched_total, undispatched_total, and calculated_refund_amount accurately.
        4. When the cashier submits a refund request, it creates a request for 155.00 (not 295.00).
        """
        self.tenant.enable_refunds = True
        self.tenant.use_strict_sales_workflow = True
        self.tenant.save()

        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.shop_location,
            attendant=self.attendant_user,
            total=Decimal("295.00"),
            amount_paid=Decimal("295.00"),
            payment_method='CASH',
            status='PENDING_DISPATCH',
        )
        # item 1: 4 ordered, 3 dispatched @ 25.00 = 75.00 dispatched, 25.00 undispatched
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            quantity=Decimal("4.00"),
            dispatched_quantity=Decimal("3.00"),
            unit_price=Decimal("25.00"),
            total=Decimal("100.00"),
        )
        # item 2: 3 ordered, 1 dispatched @ 65.00 = 65.00 dispatched, 130.00 undispatched
        p2 = Product.objects.create(
            tenant=self.tenant,
            name="Pendrive",
            sku=f"PEN-{self.unique_suffix}",
            category=self.category,
            default_selling_price=Decimal("65.00"),
        )
        SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=p2,
            quantity=Decimal("3.00"),
            dispatched_quantity=Decimal("1.00"),
            unit_price=Decimal("65.00"),
            total=Decimal("195.00"),
        )

        # Halt sale
        sale.halt_dispatch(self.manager_user, reason="Customer canceled remainder")
        self.assertTrue(sale.is_halted)
        self.assertTrue(sale.is_partially_dispatched)
        self.assertEqual(sale.dispatched_total, Decimal("140.00"))
        self.assertEqual(sale.undispatched_total, Decimal("155.00"))
        self.assertEqual(sale.calculated_refund_amount, Decimal("155.00"))

        # Verify api_sale_detail
        self.client.force_login(self.cashier_user)
        resp = self.client.get(f"/sales/api/{sale.pk}/detail/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['dispatched_total'], "140.00")
        self.assertEqual(data['undispatched_total'], "155.00")
        self.assertEqual(data['calculated_refund_amount'], "155.00")
        self.assertTrue(data['calculated_is_partial_halt'])

        # Cashier requests refund
        req_resp = self.client.post(
            f"/sales/api/{sale.pk}/refund/",
            data=json.dumps({'reason': 'Customer requested refund on undispatched items'}),
            content_type='application/json'
        )
        self.assertEqual(req_resp.status_code, 200)
        rf = RefundRequest.objects.get(sale=sale)
        self.assertEqual(rf.refund_amount, Decimal("155.00"))
        self.assertEqual(rf.dispatched_total, Decimal("140.00"))
        self.assertTrue(rf.is_partial_halt)





