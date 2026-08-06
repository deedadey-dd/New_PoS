"""
Management command to set up the Demo Company environment with realistic mock data.

Covers ALL features:
- Two tenants: standard workflow & strict cashier workflow
- Locations: Production, Stores/Warehouse, 2 Shops per tenant
- Full user roster: Admin, Auditor, Accountant, Production Manager, Stores Manager,
  Shop Managers (×2), Attendants (×2), Cashier (strict only)
- Payment providers: Paystack, NaloPay, AppsnMobile — assigned to each shop
- ECashWithdrawal records (pending + completed)
- ShopSettings (printer, receipt, payment method toggles)
- 5 Product categories (with subcategories) + 10 diverse products with batch tracking
- FEFO demo: dual-batch product with different expiry dates
- Inventory snapshots + ShopPrice entries per shop per product
- FavoriteProduct entries at each shop
- 8 Customers with realistic credit balances + CustomerTransaction history
- Shifts: one open (today), two closed (historical)
- 40 Mock Sales: CASH, ECASH, MOMO, CREDIT, MIXED — spanning last 30 days
- PENDING / PENDING_DISPATCH sales for strict workflow cashier queue
- ECashLedger entries for all ECASH/MOMO sales
- ECashWithdrawal records (completed + pending)
- RefundRequests: PENDING + APPROVED
- CashTransfers: attendant→manager, manager→accountant (PENDING + CONFIRMED)
- BankTransfers by accountant (CASH, ECASH, MOMO sources)
- DigitalFundWithdrawals by accountant
- Closed shifts with cash variance scenarios
- StockAdjustments: APPROVED (cycle count) + PENDING (damage write-off) + REJECTED
- ExpenditureCategories (seeded defaults) + ExpenditureRequests with items
  in all states (PENDING, PARTIAL, FULLY_APPROVED, REJECTED)
- Transfers (DRAFT, SENT, RECEIVED, PARTIAL, DISPUTED, CLOSED)
- StockRequests: CONVERTED, APPROVED, PENDING, REJECTED
- Inter-shop transfer (if enabled)
- Notifications: LOW_STOCK, TRANSFER_SENT, STOCK_ADJUSTMENT, SYSTEM, EXPIRY_WARNING
- BulletinBoard posts (ANNOUNCEMENT, PRICE_ALERT, GENERAL) with targets
"""

import os
import random
from decimal import Decimal
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from django.core.files import File
from django.contrib.auth import get_user_model

from apps.core.models import Tenant, Location, Role
from apps.inventory.models import (
    Category, Product, Batch, Inventory, InventoryLedger, ShopPrice,
    FavoriteProduct, StockAdjustment,
)
from apps.sales.models import Sale, SaleItem, Shift, ShopSettings, RefundRequest
from apps.accounting.models import (
    CashTransfer, BankTransfer, DigitalFundWithdrawal,
    ExpenditureCategory, ExpenditureRequest, ExpenditureItem,
)
from apps.customers.models import Customer, CustomerTransaction
from apps.payments.models import PaymentProviderConfig, ECashLedger, ShopPaymentAssignment, ECashWithdrawal
from apps.notifications.models import Notification, BulletinPost

User = get_user_model()

DEMO_TENANT_NAMES = ['Demo Company', 'Strict Demo Company']


class Command(BaseCommand):
    help = 'Sets up comprehensive Demo Company environments covering ALL system features.'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.HTTP_INFO('Starting Comprehensive Demo Environment Setup...'))

        with transaction.atomic():
            # ----------------------------------------------------------------
            # 1. Clean existing demo data
            # ----------------------------------------------------------------
            self.stdout.write('  Wiping old demo data...')
            self._wipe_demo_data()

            # ----------------------------------------------------------------
            # 2. Ensure Roles exist
            # ----------------------------------------------------------------
            roles = self._ensure_roles()

            # ----------------------------------------------------------------
            # 3. Create Tenants
            # ----------------------------------------------------------------
            self.stdout.write('  Creating Demo Tenants...')
            tenant_standard, tenant_strict = self._create_tenants()

            # ----------------------------------------------------------------
            # 4. Build full data for each tenant
            # ----------------------------------------------------------------
            for tenant, is_strict in [(tenant_standard, False), (tenant_strict, True)]:
                self.stdout.write(f'  Building data for: {tenant.name}')
                self._build_tenant_data(tenant, roles, is_strict)

        self.stdout.write(self.style.SUCCESS('\n[OK] All Demo Environments successfully created!\n'))
        self.stdout.write('  Login credentials (password: demo):')
        for name in DEMO_TENANT_NAMES:
            prefix = 'strict_' if 'Strict' in name else ''
            self.stdout.write(f'    [{name}]  admin@demo.com  /  {prefix}admin@demo.com')

    # ========================================================================
    # WIPE
    # ========================================================================

    def _wipe_demo_data(self):
        from apps.transfers.models import StockRequest, StockRequestItem, Transfer, TransferItem

        tenants = Tenant.objects.filter(name__in=DEMO_TENANT_NAMES)
        for tenant in tenants:
            # Notifications
            Notification.objects.filter(tenant=tenant).delete()
            BulletinPost.objects.filter(tenant=tenant).delete()

            # Transfers
            TransferItem.objects.filter(tenant=tenant).delete()
            Transfer.objects.filter(tenant=tenant).delete()
            StockRequestItem.objects.filter(tenant=tenant).delete()
            StockRequest.objects.filter(tenant=tenant).delete()

            # Inventory
            StockAdjustment.objects.filter(tenant=tenant).delete()
            FavoriteProduct.objects.filter(tenant=tenant).delete()
            ShopPrice.objects.filter(tenant=tenant).delete()
            InventoryLedger.objects.filter(tenant=tenant).delete()
            Inventory.objects.filter(tenant=tenant).delete()
            Batch.objects.filter(tenant=tenant).delete()

            # Sales & Refunds
            RefundRequest.objects.filter(tenant=tenant).delete()
            SaleItem.objects.filter(tenant=tenant).delete()
            Sale.objects.filter(tenant=tenant).delete()
            Shift.objects.filter(tenant=tenant).delete()

            # Accounting
            ExpenditureItem.objects.filter(tenant=tenant).delete()
            ExpenditureRequest.objects.filter(tenant=tenant).delete()
            DigitalFundWithdrawal.objects.filter(tenant=tenant).delete()
            BankTransfer.objects.filter(tenant=tenant).delete()
            CashTransfer.objects.filter(tenant=tenant).delete()

            # Customers
            CustomerTransaction.objects.filter(tenant=tenant).delete()
            Customer.objects.filter(tenant=tenant).delete()

            # Payments
            ECashWithdrawal.objects.filter(tenant=tenant).delete()
            ECashLedger.objects.filter(tenant=tenant).delete()
            ShopPaymentAssignment.objects.filter(tenant=tenant).delete()
            PaymentProviderConfig.objects.filter(tenant=tenant).delete()

            # Shop settings
            ShopSettings.objects.filter(tenant=tenant).delete()

            # Core (order matters — users before locations before tenant)
            Product.objects.filter(tenant=tenant).delete()
            Category.objects.filter(tenant=tenant).delete()
            User.objects.filter(tenant=tenant).delete()
            Location.objects.filter(tenant=tenant).delete()
            tenant.delete()

        # Catch any straggler demo users
        User.objects.filter(email__endswith='@demo.com').delete()

    # ========================================================================
    # ROLES
    # ========================================================================

    def _ensure_roles(self):
        role_names = [
            'ADMIN', 'AUDITOR', 'ACCOUNTANT',
            'PRODUCTION_MANAGER', 'STORES_MANAGER',
            'SHOP_MANAGER', 'SHOP_ATTENDANT', 'SHOP_CASHIER',
        ]
        roles = {}
        for name in role_names:
            role, _ = Role.objects.get_or_create(
                name=name, defaults={'description': f'Demo {name}'}
            )
            roles[name] = role
        return roles

    # ========================================================================
    # TENANTS
    # ========================================================================

    def _create_tenants(self):
        tenant_standard = Tenant.objects.create(
            name='Demo Company',
            email='demo@demo.com',
            currency='GHS',
            phone='0241234567',
            address='123 High Street, Accra',
            subscription_status='ACTIVE',
            # Feature flags
            allow_momo_payments=True,
            allow_inter_shop_transfers=True,
            allow_negative_stock=False,
            enable_refunds=True,
            require_refund_approval=True,
            require_return_approval=True,
            refund_always_cash=False,
            shop_manager_can_add_products=True,
            shop_manager_can_receive_stock=True,
            shops_can_see_other_stock=True,
            allow_accountant_to_shop_transfers=True,
            pricing_control_mode='SHOP_MANAGER',
            accountants_can_approve_adjustments=True,
            use_strict_sales_workflow=False,
            use_cashier_workflow=False,
            waive_shift_requirement=False,
            cashier_transfers_to_bank=False,
            credit_limit_warning_percent=80,
        )
        tenant_strict = Tenant.objects.create(
            name='Strict Demo Company',
            email='strictdemo@demo.com',
            currency='GHS',
            phone='0241234568',
            address='456 Ring Road, Kumasi',
            subscription_status='ACTIVE',
            # Feature flags — strict mode
            use_strict_sales_workflow=True,
            use_cashier_workflow=True,
            allow_momo_payments=True,
            allow_inter_shop_transfers=False,
            allow_negative_stock=False,
            enable_refunds=True,
            require_refund_approval=True,
            require_return_approval=True,
            refund_always_cash=True,
            shop_manager_can_add_products=False,
            shop_manager_can_receive_stock=False,
            shops_can_see_other_stock=False,
            allow_accountant_to_shop_transfers=False,
            pricing_control_mode='ACCOUNTANT_PER_SHOP',
            accountants_can_approve_adjustments=False,
            waive_shift_requirement=False,
            cashier_transfers_to_bank=True,
            credit_limit_warning_percent=90,
        )
        return tenant_standard, tenant_strict

    # ========================================================================
    # MAIN BUILDER
    # ========================================================================

    def _build_tenant_data(self, tenant, roles, is_strict):
        prefix = 'strict_' if is_strict else ''
        now = timezone.now()
        today = now.date()

        # --------------------------------------------------------------------
        # Locations
        # --------------------------------------------------------------------
        loc_production = Location.objects.create(
            tenant=tenant, name='Main Production', location_type='PRODUCTION',
            address='Industrial Area, Factory Lane', is_active=True
        )
        loc_stores = Location.objects.create(
            tenant=tenant, name='Central Warehouse', location_type='STORES',
            address='Warehouse District', is_active=True
        )
        loc_shop1 = Location.objects.create(
            tenant=tenant, name='Downtown Shop', location_type='SHOP',
            address='123 Main Street', phone='0241000001', is_active=True
        )
        loc_shop2 = Location.objects.create(
            tenant=tenant, name='Uptown Shop', location_type='SHOP',
            address='456 High Street', phone='0241000002', is_active=True
        )

        # --------------------------------------------------------------------
        # Users
        # --------------------------------------------------------------------
        users_spec = [
            (f'{prefix}admin@demo.com',       'Admin',       'Demo',        roles['ADMIN'],              None),
            (f'{prefix}auditor@demo.com',      'System',      'Auditor',     roles['AUDITOR'],            None),
            (f'{prefix}accountant@demo.com',   'Chief',       'Accountant',  roles['ACCOUNTANT'],         None),
            (f'{prefix}production@demo.com',   'Production',  'Manager',     roles['PRODUCTION_MANAGER'], loc_production),
            (f'{prefix}stores@demo.com',       'Warehouse',   'Manager',     roles['STORES_MANAGER'],     loc_stores),
            (f'{prefix}manager1@demo.com',     'Downtown',    'Manager',     roles['SHOP_MANAGER'],       loc_shop1),
            (f'{prefix}manager2@demo.com',     'Uptown',      'Manager',     roles['SHOP_MANAGER'],       loc_shop2),
            (f'{prefix}attendant1@demo.com',   'Downtown',    'Attendant',   roles['SHOP_ATTENDANT'],     loc_shop1),
            (f'{prefix}attendant2@demo.com',   'Uptown',      'Attendant',   roles['SHOP_ATTENDANT'],     loc_shop2),
        ]
        if is_strict:
            users_spec.append(
                (f'{prefix}cashier@demo.com', 'Downtown', 'Cashier', roles['SHOP_CASHIER'], loc_shop1)
            )

        user_map = {}
        for email, fname, lname, role, loc in users_spec:
            u = User.objects.create_user(
                email=email, password='demo',
                first_name=fname, last_name=lname,
                tenant=tenant, role=role, location=loc, is_active=True,
            )
            user_map[email] = u

        admin      = user_map[f'{prefix}admin@demo.com']
        auditor    = user_map[f'{prefix}auditor@demo.com']
        accountant = user_map[f'{prefix}accountant@demo.com']
        prod_mgr   = user_map[f'{prefix}production@demo.com']
        stores_mgr = user_map[f'{prefix}stores@demo.com']
        manager1   = user_map[f'{prefix}manager1@demo.com']
        manager2   = user_map[f'{prefix}manager2@demo.com']
        attendant1 = user_map[f'{prefix}attendant1@demo.com']
        attendant2 = user_map[f'{prefix}attendant2@demo.com']
        cashier    = user_map.get(f'{prefix}cashier@demo.com')

        # --------------------------------------------------------------------
        # Payment Provider Configs + Shop Assignments
        # --------------------------------------------------------------------
        paystack_cfg = PaymentProviderConfig.objects.create(
            tenant=tenant, provider='PAYSTACK', nickname='Main Paystack',
            is_active=True, test_mode=True,
            public_key='pk_test_demo_paystack_pub',
        )
        paystack_cfg.secret_key = 'sk_test_demo_paystack_sec'
        paystack_cfg.save()

        nalopay_cfg = PaymentProviderConfig.objects.create(
            tenant=tenant, provider='NALOPAY', nickname='Nalo Mobile',
            is_active=True, test_mode=True,
            public_key='demo_pk_nalo',
        )
        nalopay_cfg.secret_key = 'demo_sk_nalo'
        nalopay_cfg.save()

        appsnmobile_cfg = PaymentProviderConfig.objects.create(
            tenant=tenant, provider='APPSNMOBILE', nickname='AppsnMobile POS',
            is_active=True, test_mode=True,
            merchant_id='DEMO_MERCHANT_001',
        )

        demo_providers = [paystack_cfg, nalopay_cfg, appsnmobile_cfg]

        for shop, providers in [
            (loc_shop1, [paystack_cfg, nalopay_cfg]),
            (loc_shop2, [paystack_cfg, appsnmobile_cfg]),
        ]:
            for idx, pc in enumerate(providers):
                ShopPaymentAssignment.objects.create(
                    tenant=tenant, shop=shop, provider_config=pc,
                    is_default=(idx == 0), priority=idx
                )

        # --------------------------------------------------------------------
        # ShopSettings
        # --------------------------------------------------------------------
        ShopSettings.objects.create(
            tenant=tenant, shop=loc_shop1,
            receipt_printer_type='THERMAL_80MM',
            auto_print_receipts=True,
            show_logo_on_receipt=True,
            enable_cash_payment=True,
            enable_credit_payment=True,
            enable_ecash_payment=True,
            enable_momo_payment=True,
            hide_zero_stock_in_pos=False,
            receipt_header=f'Welcome to {tenant.name} — Downtown',
            receipt_footer='Thank you for shopping with us!',
        )
        ShopSettings.objects.create(
            tenant=tenant, shop=loc_shop2,
            receipt_printer_type='THERMAL_58MM',
            auto_print_receipts=False,
            show_logo_on_receipt=False,
            enable_cash_payment=True,
            enable_credit_payment=True,
            enable_ecash_payment=True,
            enable_momo_payment=True,
            hide_zero_stock_in_pos=True,
            receipt_header=f'Welcome to {tenant.name} — Uptown',
            receipt_footer='Come back soon!',
        )

        # --------------------------------------------------------------------
        # Categories (with parent/child hierarchy)
        # --------------------------------------------------------------------
        cat_food       = Category.objects.create(tenant=tenant, name='Food & Grocery')
        cat_beverages  = Category.objects.create(tenant=tenant, name='Beverages', parent=cat_food)
        cat_snacks     = Category.objects.create(tenant=tenant, name='Snacks & Confectionery', parent=cat_food)
        cat_stationery = Category.objects.create(tenant=tenant, name='Stationery & Office')
        cat_toys       = Category.objects.create(tenant=tenant, name='Toys & Games')

        # --------------------------------------------------------------------
        # Products with Batches
        # --------------------------------------------------------------------
        # Each entry: (name, cat, default_price, cost, shop1_qty, shop2_qty, stores_qty, prod_qty, reorder, expiry_days, image_filename)
        product_specs = [
            # Well-stocked (green)
            ('Blue Ink Pen',           cat_stationery, '2.50',  '1.20',  200, 150, 400, 800, 20,  None, 'blue_pen.png'),
            ('Exercise Book 80pg',     cat_stationery, '5.00',  '2.50',  120,  90, 300, 600, 15,  None, 'exercise_book.png'),
            # Beverages with expiry
            ('Fresh Cola 500ml',       cat_beverages,  '5.00',  '2.80',   80,  70, 200, 400, 15,    60, 'fresh_cola.png'),
            ('Bottled Water 500ml',    cat_beverages,  '2.00',  '0.80',   60,  50, 200, 400, 25,    90, 'bottled_water.webp'),
            # Snacks (dual batch for FEFO demo)
            ('Chocolate Biscuit 200g', cat_snacks,     '6.00',  '3.50',    8,   5,  40,  80, 10,    45, 'chocolate_biscuit.png'),
            ('Potato Crisps 150g',     cat_snacks,     '4.50',  '2.00',   35,  30, 100, 200, 12,    75, 'potato_crisps.webp'),
            # Toys
            ('Action Figure (small)',  cat_toys,       '45.00', '20.00',  14,  20,  40,  80,  8,  None, None),
            ('Puzzle 100-piece',       cat_toys,       '32.00', '14.00',  22,  18,  60, 120,  5,  None, None),
            # Extra critical stock (red)
            ('Red Ink Pen',            cat_stationery,  '2.50',  '1.20',   3,   2,  10,  20, 10,  None, 'red_ink_pen.webp'),
            # High-value
            ('Scientific Calculator',  cat_stationery, '55.00', '30.00',  12,   8,  30,  60,  3,  None, 'scientific_calculator.webp'),
        ]

        products = []
        for spec in product_specs:
            name, cat, price, cost, stock1, stock2, stores_qty, prod_qty, reorder, expiry_days, img_file = spec
            sku = (name.replace(' ', '').upper()[:10])
            p = Product.objects.create(
                tenant=tenant, category=cat, name=name, sku=sku,
                default_selling_price=Decimal(price),
                is_active=True,
                reorder_level=Decimal(str(reorder)),
            )

            if img_file:
                p.image.name = f"products/{img_file}"
                p.save(update_fields=['image'])

            expiry_date = (today + timedelta(days=expiry_days)) if expiry_days else None

            # Stores batch (primary)
            batch_stores = Batch.objects.create(
                tenant=tenant, product=p, location=loc_stores,
                batch_number=f'BATCH-{sku}-001',
                unit_cost=Decimal(cost),
                initial_quantity=Decimal(str(stores_qty)),
                current_quantity=Decimal('0'),
                received_date=today - timedelta(days=random.randint(10, 45)),
                expiry_date=expiry_date,
                status='AVAILABLE',
            )

            # FEFO demo: give Chocolate Biscuit a second, older, almost-expired batch at stores
            batch_stores_old = None
            if name == 'Chocolate Biscuit 200g':
                old_expiry = today + timedelta(days=8)  # nearly expired
                batch_stores_old = Batch.objects.create(
                    tenant=tenant, product=p, location=loc_stores,
                    batch_number=f'BATCH-{sku}-000',
                    unit_cost=Decimal('3.20'),
                    initial_quantity=Decimal('20'),
                    current_quantity=Decimal('0'),
                    received_date=today - timedelta(days=90),
                    expiry_date=old_expiry,
                    status='AVAILABLE',
                )

            # Shop 1 batch
            batch_shop1 = Batch.objects.create(
                tenant=tenant, product=p, location=loc_shop1,
                batch_number=f'BATCH-{sku}-001',
                unit_cost=Decimal(cost),
                initial_quantity=Decimal(str(stock1)),
                current_quantity=Decimal('0'),
                received_date=today - timedelta(days=random.randint(1, 10)),
                expiry_date=expiry_date,
                status='AVAILABLE',
            )

            # Shop 2 batch
            batch_shop2 = Batch.objects.create(
                tenant=tenant, product=p, location=loc_shop2,
                batch_number=f'BATCH-{sku}-001',
                unit_cost=Decimal(cost),
                initial_quantity=Decimal(str(stock2)),
                current_quantity=Decimal('0'),
                received_date=today - timedelta(days=random.randint(1, 10)),
                expiry_date=expiry_date,
                status='AVAILABLE',
            )

            # Production batch
            batch_prod = Batch.objects.create(
                tenant=tenant, product=p, location=loc_production,
                batch_number=f'BATCH-{sku}-001',
                unit_cost=Decimal(cost),
                initial_quantity=Decimal(str(prod_qty)),
                current_quantity=Decimal('0'),
                received_date=today - timedelta(days=random.randint(15, 60)),
                expiry_date=expiry_date,
                status='AVAILABLE',
            )

            products.append({
                'product': p,
                'stock1': stock1, 'stock2': stock2,
                'stores_qty': stores_qty, 'prod_qty': prod_qty,
                'batch_shop1': batch_shop1, 'batch_shop2': batch_shop2,
                'batch_stores': batch_stores, 'batch_prod': batch_prod,
                'batch_stores_old': batch_stores_old,
                'cost': Decimal(cost),
            })

        # --------------------------------------------------------------------
        # Inventory Snapshots + ShopPrices
        # --------------------------------------------------------------------
        for item in products:
            p = item['product']

            # Shops
            for shop, qty, batch in [
                (loc_shop1, item['stock1'], item['batch_shop1']),
                (loc_shop2, item['stock2'], item['batch_shop2']),
            ]:
                InventoryLedger.objects.create(
                    tenant=tenant, product=p, batch=batch,
                    location=shop, transaction_type='IN',
                    quantity=Decimal(str(qty)),
                    unit_cost=batch.unit_cost,
                    reference_type='DEMO-SEED',
                    notes='Initial demo stock',
                )
                Inventory.objects.create(
                    tenant=tenant, location=shop, product=p, quantity=Decimal(str(qty))
                )
                ShopPrice.objects.create(
                    tenant=tenant, location=shop, product=p,
                    selling_price=p.default_selling_price,
                )

            # Stores
            InventoryLedger.objects.create(
                tenant=tenant, product=p, batch=item['batch_stores'],
                location=loc_stores, transaction_type='IN',
                quantity=Decimal(str(item['stores_qty'])),
                unit_cost=item['batch_stores'].unit_cost,
                reference_type='DEMO-SEED',
                notes='Initial warehouse stock',
            )
            Inventory.objects.create(
                tenant=tenant, location=loc_stores, product=p,
                quantity=Decimal(str(item['stores_qty']))
            )

            # Production
            InventoryLedger.objects.create(
                tenant=tenant, product=p, batch=item['batch_prod'],
                location=loc_production, transaction_type='IN',
                quantity=Decimal(str(item['prod_qty'])),
                unit_cost=item['batch_prod'].unit_cost,
                reference_type='DEMO-SEED',
                notes='Initial production stock',
            )
            Inventory.objects.create(
                tenant=tenant, location=loc_production, product=p,
                quantity=Decimal(str(item['prod_qty']))
            )

        # --------------------------------------------------------------------
        # FavoriteProducts
        # --------------------------------------------------------------------
        favorites = ['Blue Ink Pen', 'Fresh Cola 500ml', 'Chocolate Biscuit 200g', 'Bottled Water 500ml']
        for item in products:
            if item['product'].name in favorites:
                for shop, mgr in [(loc_shop1, manager1), (loc_shop2, manager2)]:
                    FavoriteProduct.objects.get_or_create(
                        tenant=tenant, location=shop, product=item['product'],
                        defaults={'created_by': mgr}
                    )

        # --------------------------------------------------------------------
        # Customers
        # --------------------------------------------------------------------
        customer_specs = [
            ('Kwame Asante',   '0241111111', 'kwame@email.com',   loc_shop1, Decimal('120.00'), Decimal('500.00')),
            ('Ama Serwah',     '0242222222', 'ama@email.com',     loc_shop1, Decimal('45.50'),  Decimal('200.00')),
            ('Kofi Mensah',    '0243333333', '',                  loc_shop1, Decimal('0.00'),   Decimal('300.00')),
            ('Abena Osei',     '0247777777', '',                  loc_shop1, Decimal('85.00'),  Decimal('250.00')),
            ('Akua Boateng',   '0244444444', 'akua@email.com',    loc_shop2, Decimal('230.00'), Decimal('500.00')),
            ('Yaw Darko',      '0245555555', '',                  loc_shop2, Decimal('15.00'),  Decimal('100.00')),
            ('Efua Nyarko',    '0246666666', 'efua@email.com',    loc_shop2, Decimal('0.00'),   Decimal('150.00')),
            ('Kojo Owusu',     '0248888888', '',                  loc_shop2, Decimal('310.00'), Decimal('400.00')),
        ]
        customer_map = {}
        for cname, cphone, cemail, cshop, balance, limit in customer_specs:
            attendant = attendant1 if cshop == loc_shop1 else attendant2
            customer = Customer.objects.create(
                tenant=tenant, name=cname, phone=cphone, email=cemail,
                shop=cshop, current_balance=balance,
                credit_limit=limit, is_active=True,
            )
            customer_map[(cname, cshop.pk)] = customer
            if balance > Decimal('0'):
                CustomerTransaction.objects.create(
                    tenant=tenant, customer=customer,
                    transaction_type='DEBIT', amount=balance,
                    description='Credit purchase (demo seed)',
                    balance_before=Decimal('0.00'), balance_after=balance,
                    performed_by=attendant,
                )

        # --------------------------------------------------------------------
        # Shifts
        # --------------------------------------------------------------------
        # Open shift (today — attendant1)
        open_shift = Shift.objects.create(
            tenant=tenant, shop=loc_shop1, attendant=attendant1,
            start_time=now - timedelta(hours=6),
            status='OPEN', opening_cash=Decimal('50.00'),
        )

        # Closed shift from yesterday (attendant1) — no variance
        closed_shift_1 = Shift.objects.create(
            tenant=tenant, shop=loc_shop1, attendant=attendant1,
            start_time=now - timedelta(days=1, hours=8),
            end_time=now - timedelta(days=1),
            status='CLOSED',
            opening_cash=Decimal('50.00'),
            closing_cash=Decimal('285.00'),
            notes='Normal day, cash balanced.',
        )

        # Closed shift from 3 days ago (attendant2 at shop2) — small shortage
        closed_shift_2 = Shift.objects.create(
            tenant=tenant, shop=loc_shop2, attendant=attendant2,
            start_time=now - timedelta(days=3, hours=9),
            end_time=now - timedelta(days=3),
            status='CLOSED',
            opening_cash=Decimal('30.00'),
            closing_cash=Decimal('198.50'),
            notes='Minor shortfall of GHS 5 — counted twice.',
        )

        # --------------------------------------------------------------------
        # Mock Sales (40 sales over last 30 days — all payment methods)
        # --------------------------------------------------------------------
        payment_pool = ['CASH', 'CASH', 'CASH', 'ECASH', 'MOMO', 'CREDIT', 'MIXED']
        completed_sales = []

        for i in range(40):
            days_ago  = random.randint(0, 29)
            sale_time = now - timedelta(days=days_ago, hours=random.randint(1, 10))
            shop      = random.choice([loc_shop1, loc_shop2])
            attendant = attendant1 if shop == loc_shop1 else attendant2
            payment_method = random.choice(payment_pool)

            # Strict workflow: some today's downtown sales stay PENDING
            status   = 'COMPLETED'
            _cashier = None
            if is_strict and days_ago == 0 and shop == loc_shop1:
                status = random.choice(['PENDING', 'PENDING_DISPATCH', 'COMPLETED'])
                if status in ('COMPLETED', 'PENDING_DISPATCH'):
                    _cashier = cashier

            # Credit/Mixed sales need a customer
            sale_customer = None
            if payment_method in ('CREDIT', 'MIXED'):
                shop_customers = Customer.objects.filter(tenant=tenant, shop=shop, is_active=True)
                if shop_customers.exists():
                    sale_customer = random.choice(list(shop_customers))
                else:
                    payment_method = 'CASH'

            shift_for_sale = open_shift if (shop == loc_shop1 and days_ago == 0) else (
                closed_shift_1 if (shop == loc_shop1 and days_ago == 1) else None
            )

            is_confirmed = False
            if payment_method in ('ECASH', 'MOMO') and status in ('COMPLETED', 'PENDING_DISPATCH'):
                is_confirmed = random.choice([True, True, False]) # 66% confirmed

            sale = Sale.objects.create(
                tenant=tenant, shop=shop, attendant=attendant,
                cashier=_cashier,
                shift=shift_for_sale,
                status=status,
                payment_method=payment_method,
                amount_paid=Decimal('0'),
                sale_number=f'DEMO-SL-{tenant.pk}-{i:04d}',
                customer=sale_customer,
                dispatched_by=attendant if status == 'COMPLETED' else None,
                dispatched_at=sale_time if status == 'COMPLETED' else None,
                is_dispatched=(status == 'COMPLETED'),
                is_accountant_confirmed=is_confirmed,
            )
            Sale.objects.filter(pk=sale.pk).update(created_at=sale_time)

            sale_total = Decimal('0')
            num_items = random.randint(1, 4)
            for _ in range(num_items):
                pitem = random.choice(products)
                p = pitem['product']
                qty = Decimal(str(random.randint(1, 5)))
                unit_price = p.default_selling_price
                item_total = unit_price * qty
                batch = pitem['batch_shop1'] if shop == loc_shop1 else pitem['batch_shop2']
                SaleItem.objects.create(
                    tenant=tenant, sale=sale, product=p,
                    quantity=qty, unit_price=unit_price,
                    unit_cost=batch.unit_cost,
                    total=item_total,
                )
                sale_total += item_total

            sale.total = sale_total
            sale.subtotal = sale_total
            if status == 'COMPLETED':
                if payment_method == 'CREDIT':
                    sale.amount_paid = Decimal('0')
                elif payment_method == 'MIXED':
                    partial = (sale_total / 2).quantize(Decimal('0.01'))
                    sale.amount_paid = partial
                else:
                    sale.amount_paid = sale_total
            sale.save()

            # ECashLedger entries for ECASH / MOMO sales
            if payment_method in ('ECASH', 'MOMO') and status == 'COMPLETED':
                provider_config = paystack_cfg if payment_method == 'ECASH' else nalopay_cfg
                ECashLedger.objects.create(
                    tenant=tenant, shop=shop,
                    transaction_type='PAYMENT',
                    amount=sale_total,
                    reference_type='Sale', reference_id=sale.pk,
                    provider_config=provider_config,
                    provider=provider_config.provider,
                    created_by=attendant,
                    notes=f'Demo {payment_method} sale',
                )
                ECashLedger.objects.filter(
                    reference_type='Sale', reference_id=sale.pk
                ).update(created_at=sale_time)

            if status == 'COMPLETED':
                completed_sales.append(sale)

        # --------------------------------------------------------------------
        # Additional PENDING sales for cashier queue (strict workflow)
        # --------------------------------------------------------------------
        if is_strict:
            for i in range(5):
                pending_sale = Sale.objects.create(
                    tenant=tenant, shop=loc_shop1, attendant=attendant1,
                    shift=open_shift, status='PENDING',
                    payment_method=random.choice(['CASH', 'ECASH']),
                    amount_paid=Decimal('0'),
                    sale_number=f'DEMO-SL-PQ-{tenant.pk}-{i:04d}',
                )
                p_total = Decimal('0')
                for _ in range(random.randint(1, 3)):
                    pitem = random.choice(products)
                    p = pitem['product']
                    qty = Decimal(str(random.randint(1, 3)))
                    it = p.default_selling_price * qty
                    SaleItem.objects.create(
                        tenant=tenant, sale=pending_sale, product=p,
                        quantity=qty, unit_price=p.default_selling_price,
                        unit_cost=pitem['batch_shop1'].unit_cost, total=it,
                    )
                    p_total += it
                pending_sale.total = p_total
                pending_sale.subtotal = p_total
                pending_sale.save()

        # --------------------------------------------------------------------
        # ECashWithdrawals (completed + pending)
        # --------------------------------------------------------------------
        # Completed withdrawal — accountant pulled from shop1
        ew_completed = ECashWithdrawal.objects.create(
            tenant=tenant, shop=loc_shop1,
            provider_config=paystack_cfg,
            withdrawn_by=accountant,
            amount=Decimal('250.00'),
            status='COMPLETED',
            completed_at=now - timedelta(days=5),
            notes='Weekly e-cash collection from downtown shop',
        )
        # Pending withdrawal awaiting approval
        ECashWithdrawal.objects.create(
            tenant=tenant, shop=loc_shop2,
            provider_config=nalopay_cfg,
            withdrawn_by=accountant,
            amount=Decimal('180.00'),
            status='PENDING',
            notes='Pending: uptown shop e-cash pull',
        )

        # --------------------------------------------------------------------
        # RefundRequests
        # --------------------------------------------------------------------
        refundable = [s for s in completed_sales if s.status == 'COMPLETED']
        if len(refundable) >= 2:
            pending_sale = refundable[0]
            req_by_1 = cashier if (is_strict and pending_sale.shop == loc_shop1 and cashier) else (manager1 if pending_sale.shop == loc_shop1 else manager2)
            RefundRequest.objects.create(
                tenant=tenant,
                sale=pending_sale,
                requested_by=req_by_1,
                reason='Customer returned item — wrong size (demo).',
                status='PENDING',
            )
            approved_sale = refundable[1]
            req_by_2 = cashier if (is_strict and approved_sale.shop == loc_shop1 and cashier) else (manager1 if approved_sale.shop == loc_shop1 else manager2)
            RefundRequest.objects.create(
                tenant=tenant,
                sale=approved_sale,
                requested_by=req_by_2,
                reason='Damaged goods returned by customer (demo).',
                status='APPROVED',
                reviewed_by=accountant,
                reviewed_at=now - timedelta(days=2),
            )
        if len(refundable) >= 3:
            rejected_sale = refundable[2]
            req_by_3 = cashier if (is_strict and rejected_sale.shop == loc_shop1 and cashier) else (manager1 if rejected_sale.shop == loc_shop1 else manager2)
            RefundRequest.objects.create(
                tenant=tenant,
                sale=rejected_sale,
                requested_by=req_by_3,
                reason='Customer changed mind after 14 days.',
                status='REJECTED',
                reviewed_by=accountant,
                reviewed_at=now - timedelta(days=7),
            )

        # --------------------------------------------------------------------
        # CashTransfers
        # --------------------------------------------------------------------
        # Attendant → Manager (shift closing style)
        for i in range(4):
            days_ago = random.randint(1, 20)
            t_time = now - timedelta(days=days_ago)
            ct = CashTransfer.objects.create(
                tenant=tenant,
                from_user=attendant1,
                from_location=loc_shop1,
                to_user=manager1,
                to_location=loc_shop1,
                transfer_type='DEPOSIT',
                destination='ACCOUNTANT',
                amount=Decimal(str(random.randint(60, 300))),
                status='CONFIRMED',
                confirmed_at=t_time,
                confirmed_by=manager1,
                notes=f'Shift closing deposit - Shift #{open_shift.pk}',
            )
            CashTransfer.objects.filter(pk=ct.pk).update(created_at=t_time)

        # Manager → Accountant (CONFIRMED + PENDING)
        for i, (shop, mgr, status) in enumerate([
            (loc_shop1, manager1, 'CONFIRMED'),
            (loc_shop2, manager2, 'CONFIRMED'),
            (loc_shop1, manager1, 'PENDING'),
            (loc_shop2, manager2, 'CONFIRMED'),
            (loc_shop1, manager1, 'CONFIRMED'),
        ]):
            days_ago = random.randint(1, 20)
            t_time = now - timedelta(days=days_ago)
            ct = CashTransfer.objects.create(
                tenant=tenant,
                from_user=mgr,
                from_location=shop,
                to_user=accountant,
                transfer_type='DEPOSIT',
                destination='ACCOUNTANT',
                amount=Decimal(str(random.randint(100, 600))),
                status=status,
                confirmed_at=t_time if status == 'CONFIRMED' else None,
                confirmed_by=accountant if status == 'CONFIRMED' else None,
            )
            CashTransfer.objects.filter(pk=ct.pk).update(created_at=t_time)

        # Float: accountant → shop manager (if enabled)
        if tenant.allow_accountant_to_shop_transfers:
            ct = CashTransfer.objects.create(
                tenant=tenant,
                from_user=accountant,
                to_user=manager1,
                to_location=loc_shop1,
                transfer_type='FLOAT',
                destination='ACCOUNTANT',
                amount=Decimal('200.00'),
                status='CONFIRMED',
                confirmed_at=now - timedelta(days=10),
                confirmed_by=manager1,
                notes='Float for busy trading period',
            )

        # --------------------------------------------------------------------
        # BankTransfers (accountant deposits to bank)
        # --------------------------------------------------------------------
        for i in range(5):
            days_ago = random.randint(1, 25)
            t_time = now - timedelta(days=days_ago)
            fund_source = random.choice(['CASH', 'ECASH', 'MOMO'])
            pc = paystack_cfg if fund_source == 'ECASH' else (nalopay_cfg if fund_source == 'MOMO' else None)
            
            actor = accountant
            if is_strict and tenant.cashier_transfers_to_bank and cashier and i < 2:
                actor = cashier
                
            bt = BankTransfer.objects.create(
                tenant=tenant,
                accountant=actor,
                amount=Decimal(str(random.randint(150, 1200))),
                fund_source=fund_source,
                provider_config=pc,
                teller_name=f'Teller {random.randint(1, 5)}',
                notes='Daily bank deposit (demo)',
            )
            BankTransfer.objects.filter(pk=bt.pk).update(created_at=t_time)

        # --------------------------------------------------------------------
        # DigitalFundWithdrawals
        # --------------------------------------------------------------------
        for i in range(3):
            days_ago = random.randint(2, 18)
            t_time = now - timedelta(days=days_ago)
            shop = random.choice([loc_shop1, loc_shop2])
            fund_source = random.choice(['ECASH', 'MOMO'])
            pc = random.choice([paystack_cfg, nalopay_cfg])
            dfw = DigitalFundWithdrawal.objects.create(
                tenant=tenant, shop=shop, accountant=accountant,
                amount=Decimal(str(random.randint(50, 400))),
                fund_source=fund_source,
                provider_config=pc,
                notes=f'Demo {fund_source} withdrawal from {shop.name}',
            )
            DigitalFundWithdrawal.objects.filter(pk=dfw.pk).update(created_at=t_time)

        # --------------------------------------------------------------------
        # Expenditure Categories (seed defaults)
        # --------------------------------------------------------------------
        ExpenditureCategory.seed_defaults(tenant)
        cat_office, _ = ExpenditureCategory.objects.get_or_create(
            tenant=tenant, name='Office Supplies', defaults={'is_default': False}
        )
        cat_maint, _ = ExpenditureCategory.objects.get_or_create(
            tenant=tenant, name='Maintenance', defaults={'is_default': False}
        )
        cat_transport, _ = ExpenditureCategory.objects.get_or_create(
            tenant=tenant, name='Transportation', defaults={'is_default': False}
        )
        all_exp_cats = [cat_office, cat_maint, cat_transport]

        # --------------------------------------------------------------------
        # Expenditure Requests (FULLY_APPROVED, PARTIAL, PENDING, REJECTED)
        # --------------------------------------------------------------------
        exp_scenarios = [
            # (status, num_items, item_statuses)
            ('FULLY_APPROVED', 2, ['APPROVED', 'APPROVED']),
            ('FULLY_APPROVED', 3, ['APPROVED', 'APPROVED', 'APPROVED']),
            ('PARTIAL',        2, ['APPROVED', 'PENDING']),
            ('PENDING',        2, ['PENDING', 'PENDING']),
            ('REJECTED',       1, ['REJECTED']),
            ('FULLY_APPROVED', 1, ['APPROVED']),
        ]
        tenant_tag = f'DEMO-T{tenant.pk}'
        for exp_idx, (req_status, num_items, item_statuses) in enumerate(exp_scenarios):
            days_ago = random.randint(0, 20)
            exp_time = now - timedelta(days=days_ago, hours=random.randint(0, 8))
            shop = random.choice([loc_shop1, loc_shop2])
            mgr = manager1 if shop == loc_shop1 else manager2

            voucher = f'{tenant_tag}-EXP-{exp_idx + 1:04d}'
            req = ExpenditureRequest.objects.create(
                tenant=tenant, location=shop, requested_by=mgr,
                status=req_status, voucher_number=voucher,
                notes=f'Demo expenditure voucher #{exp_idx + 1}',
            )
            ExpenditureRequest.objects.filter(pk=req.pk).update(created_at=exp_time)

            for item_status in item_statuses:
                ExpenditureItem.objects.create(
                    tenant=tenant, request=req,
                    category=random.choice(all_exp_cats),
                    description=f'Demo expenditure: {random.choice(["Paper", "Pens", "Transport", "Cleaning", "Repair"])}',
                    amount=Decimal(str(random.randint(15, 300))),
                    status=item_status,
                    source_of_funds='SHOP_CASH' if item_status == 'APPROVED' else None,
                    approved_by=accountant if item_status in ('APPROVED', 'REJECTED') else None,
                    approved_at=exp_time if item_status in ('APPROVED', 'REJECTED') else None,
                    rejection_reason='Out of budget.' if item_status == 'REJECTED' else '',
                )

        # --------------------------------------------------------------------
        # Stock Adjustments (APPROVED, PENDING, REJECTED)
        # --------------------------------------------------------------------
        adj_pen = products[0]  # Blue Ink Pen — well stocked
        adj_biscuit = products[4]  # Chocolate Biscuit — low stock

        adj_reviewer = auditor if is_strict else accountant

        # Approved positive adjustment (cycle count)
        StockAdjustment.objects.create(
            tenant=tenant,
            product=adj_pen['product'],
            batch=adj_pen['batch_shop1'],
            location=loc_shop1,
            adjustment_type='ADJUST',
            quantity=Decimal('15'),
            reason='Cycle count correction — 15 units found during stock take.',
            status='APPROVED',
            requested_by=manager1,
            reviewed_by=adj_reviewer,
            reviewed_at=now - timedelta(days=4),
        )

        # Pending damage write-off
        StockAdjustment.objects.create(
            tenant=tenant,
            product=adj_biscuit['product'],
            batch=adj_biscuit['batch_shop1'],
            location=loc_shop1,
            adjustment_type='DAMAGE',
            quantity=Decimal('-3'),
            reason='Water damage during storage (demo pending).',
            status='PENDING',
            requested_by=manager1,
        )

        # Rejected adjustment
        StockAdjustment.objects.create(
            tenant=tenant,
            product=products[2]['product'],  # Fresh Cola
            batch=products[2]['batch_shop2'],
            location=loc_shop2,
            adjustment_type='ADJUST',
            quantity=Decimal('50'),
            reason='Attempted phantom stock inflation.',
            status='REJECTED',
            requested_by=manager2,
            reviewed_by=adj_reviewer,
            reviewed_at=now - timedelta(days=8),
            review_notes='Rejected: unverified count without physical audit.',
        )

        # --------------------------------------------------------------------
        # Stock Requests + Transfers
        # --------------------------------------------------------------------
        from apps.transfers.models import StockRequest, StockRequestItem, Transfer, TransferItem

        pen = products[0]
        cola = products[2]
        biscuit = products[4]
        water = products[3]

        # ── Request 1: Shop1 → Stores (CONVERTED → RECEIVED transfer) ──────
        req1 = StockRequest.objects.create(
            tenant=tenant,
            requesting_location=loc_shop1,
            supplying_location=loc_stores,
            status='CONVERTED',
            requested_by=manager1,
            approved_by=stores_mgr,
            approved_at=now - timedelta(days=10),
            notes='Restocking Blue Pens for busy week ahead.',
        )
        StockRequestItem.objects.create(
            tenant=tenant, request=req1, product=pen['product'],
            quantity_requested=Decimal('100'),
        )
        transfer1 = Transfer.objects.create(
            tenant=tenant,
            source_location=loc_stores,
            destination_location=loc_shop1,
            status='RECEIVED',
            created_by=stores_mgr,
            sent_by=stores_mgr, sent_at=now - timedelta(days=9),
            received_by=manager1, received_at=now - timedelta(days=8),
            notes='Fulfilling REQ for Blue Pens.',
        )
        req1.resulting_transfer = transfer1
        req1.save(update_fields=['resulting_transfer'])
        TransferItem.objects.create(
            tenant=tenant, transfer=transfer1,
            product=pen['product'],
            batch=pen['batch_stores'],
            quantity_requested=Decimal('100'),
            quantity_sent=Decimal('100'),
            quantity_received=Decimal('100'),
            unit_cost=pen['cost'],
        )

        # ── Request 2: Stores → Production (APPROVED, not yet converted) ───
        req2 = StockRequest.objects.create(
            tenant=tenant,
            requesting_location=loc_stores,
            supplying_location=loc_production,
            status='APPROVED',
            requested_by=stores_mgr,
            approved_by=prod_mgr,
            approved_at=now - timedelta(days=2),
            notes='Running low on Fresh Cola — need new production batch.',
        )
        StockRequestItem.objects.create(
            tenant=tenant, request=req2, product=cola['product'],
            quantity_requested=Decimal('200'),
        )

        # ── Request 3: Shop2 → Stores (PENDING) ─────────────────────────────
        req3 = StockRequest.objects.create(
            tenant=tenant,
            requesting_location=loc_shop2,
            supplying_location=loc_stores,
            status='PENDING',
            requested_by=manager2,
            notes='Running critically low on Chocolate Biscuits.',
        )
        StockRequestItem.objects.create(
            tenant=tenant, request=req3, product=biscuit['product'],
            quantity_requested=Decimal('30'),
        )

        # ── Request 4: Shop1 → Stores (REJECTED) ────────────────────────────
        req4 = StockRequest.objects.create(
            tenant=tenant,
            requesting_location=loc_shop1,
            supplying_location=loc_stores,
            status='REJECTED',
            requested_by=manager1,
            approved_by=stores_mgr,
            approved_at=now - timedelta(days=15),
            rejection_reason='Warehouse insufficient stock — wait for Production batch.',
            notes='Request for extra water bottles.',
        )
        StockRequestItem.objects.create(
            tenant=tenant, request=req4, product=water['product'],
            quantity_requested=Decimal('150'),
        )

        # ── Transfer: Production → Stores (SENT — in transit) ───────────────
        transfer2 = Transfer.objects.create(
            tenant=tenant,
            source_location=loc_production,
            destination_location=loc_stores,
            status='SENT',
            created_by=prod_mgr,
            sent_by=prod_mgr, sent_at=now - timedelta(hours=4),
            notes='Weekly production output — Fresh Cola batch.',
        )
        TransferItem.objects.create(
            tenant=tenant, transfer=transfer2,
            product=cola['product'],
            batch=cola['batch_prod'],
            quantity_requested=Decimal('200'),
            quantity_sent=Decimal('200'),
            quantity_received=Decimal('0'),
            unit_cost=cola['cost'],
        )

        # ── Transfer: Stores → Shop2 (PARTIAL — short received) ─────────────
        transfer3 = Transfer.objects.create(
            tenant=tenant,
            source_location=loc_stores,
            destination_location=loc_shop2,
            status='PARTIAL',
            created_by=stores_mgr,
            sent_by=stores_mgr, sent_at=now - timedelta(days=6),
            received_by=manager2, received_at=now - timedelta(days=5),
            notes='Partial receipt — some biscuits damaged in transit.',
        )
        TransferItem.objects.create(
            tenant=tenant, transfer=transfer3,
            product=biscuit['product'],
            batch=biscuit['batch_stores'],
            quantity_requested=Decimal('20'),
            quantity_sent=Decimal('20'),
            quantity_received=Decimal('14'),
            unit_cost=biscuit['cost'],
            discrepancy_reason='DAMAGED',
            discrepancy_notes='6 packs crushed during transport.',
        )

        # ── Transfer: Shop1 → Shop2 (inter-shop, CLOSED — only if enabled) ──
        if tenant.allow_inter_shop_transfers:
            transfer4 = Transfer.objects.create(
                tenant=tenant,
                source_location=loc_shop1,
                destination_location=loc_shop2,
                status='CLOSED',
                created_by=manager1,
                sent_by=manager1, sent_at=now - timedelta(days=12),
                received_by=manager2, received_at=now - timedelta(days=11),
                notes='Inter-shop transfer: spare pens from downtown to uptown.',
                resolution_notes='Fully reconciled.',
            )
            TransferItem.objects.create(
                tenant=tenant, transfer=transfer4,
                product=pen['product'],
                batch=pen['batch_shop1'],
                quantity_requested=Decimal('30'),
                quantity_sent=Decimal('30'),
                quantity_received=Decimal('30'),
                unit_cost=pen['cost'],
            )

        # ── Draft Transfer (not yet sent) ────────────────────────────────────
        transfer5 = Transfer.objects.create(
            tenant=tenant,
            source_location=loc_stores,
            destination_location=loc_shop1,
            status='DRAFT',
            created_by=stores_mgr,
            notes='Prepared but not yet dispatched.',
        )
        TransferItem.objects.create(
            tenant=tenant, transfer=transfer5,
            product=water['product'],
            batch=water['batch_stores'],
            quantity_requested=Decimal('50'),
            quantity_sent=Decimal('0'),
            quantity_received=Decimal('0'),
            unit_cost=water['cost'],
        )

        # --------------------------------------------------------------------
        # Notifications
        # --------------------------------------------------------------------
        # Low stock alert → manager
        Notification.objects.create(
            tenant=tenant, user=manager1,
            title='Low Stock Alert: Chocolate Biscuit 200g',
            message='Chocolate Biscuit 200g at Downtown Shop has fallen to 8 units (reorder level: 10). Please request a restock.',
            notification_type='LOW_STOCK',
            reference_type='Product',
            reference_id=adj_biscuit['product'].pk,
        )
        Notification.objects.create(
            tenant=tenant, user=manager2,
            title='Low Stock Alert: Red Ink Pen',
            message='Red Ink Pen at Uptown Shop has fallen to 2 units (reorder level: 10). Please request a restock.',
            notification_type='LOW_STOCK',
            reference_type='Product',
            reference_id=products[8]['product'].pk,
        )

        # Expiry warning → stores manager
        Notification.objects.create(
            tenant=tenant, user=stores_mgr,
            title='Expiry Warning: Chocolate Biscuit 200g',
            message='Batch BATCH-CHOCO-000 for Chocolate Biscuit 200g expires in 8 days. Please dispatch or write off.',
            notification_type='EXPIRY_WARNING',
        )

        # Transfer sent → manager1 (downtown received a transfer)
        Notification.objects.create(
            tenant=tenant, user=manager1,
            title=f'Transfer {transfer1.transfer_number} Received',
            message=f'Central Warehouse has dispatched 100 units of Blue Ink Pen to Downtown Shop.',
            notification_type='TRANSFER_SENT',
            reference_type='Transfer',
            reference_id=transfer1.pk,
        )

        # Stock adjustment pending → admin/auditor
        Notification.objects.create(
            tenant=tenant, user=auditor,
            title='Stock Adjustment Pending Approval',
            message='Manager Downtown has requested a damage write-off for 3 units of Chocolate Biscuit 200g. Please review.',
            notification_type='STOCK_ADJUSTMENT',
        )

        # Cash transfer confirmed → manager1
        Notification.objects.create(
            tenant=tenant, user=manager1,
            title='Cash Transfer Confirmed',
            message=f'Your cash transfer to {accountant.get_full_name()} has been confirmed.',
            notification_type='SYSTEM',
            is_read=True,
        )

        # Unread system notice for accountant
        Notification.objects.create(
            tenant=tenant, user=accountant,
            title='New Cash Transfer Pending Confirmation',
            message=f'Downtown Manager has submitted a cash deposit for your confirmation.',
            notification_type='SYSTEM',
        )

        # --------------------------------------------------------------------
        # Bulletin Board Posts
        # --------------------------------------------------------------------
        mgr_role = roles['SHOP_MANAGER']
        attendant_role = roles['SHOP_ATTENDANT']
        accountant_role = roles['ACCOUNTANT']

        # Pinned announcement — everyone
        bp1 = BulletinPost.objects.create(
            tenant=tenant, created_by=admin,
            title='New Pricing Policy Effective Next Week',
            body='<p>All shops must update their prices before Monday using the Shop Price feature. '
                 'Contact the accountant for approved price sheets.</p>',
            post_type='ANNOUNCEMENT',
            is_pinned=True,
            is_active=True,
        )

        # Price alert for managers and attendants only
        bp2 = BulletinPost.objects.create(
            tenant=tenant, created_by=admin,
            title='Fresh Cola 500ml — Price Increase by GHS 0.50',
            body='<p>Fresh Cola 500ml price has been updated from GHS 5.00 to GHS 5.50 effective today. '
                 'Please ensure your POS reflects the new price.</p>',
            post_type='PRICE_ALERT',
            is_pinned=False,
            is_active=True,
        )
        bp2.target_roles.set([mgr_role, attendant_role])

        # General post (older, expired)
        bp3 = BulletinPost.objects.create(
            tenant=tenant, created_by=manager1,
            title='Shop Cleaning Day Reminder',
            body='<p>All staff: the shop will be closed for deep cleaning this Saturday. '
                 'Please ensure all stock is counted before closing on Friday.</p>',
            post_type='GENERAL',
            is_pinned=False,
            is_active=False,
            expires_at=now - timedelta(days=3),
        )
        bp3.target_locations.set([loc_shop1])

        # System update for accountants
        bp4 = BulletinPost.objects.create(
            tenant=tenant, created_by=admin,
            title='New Feature: E-Cash Withdrawal Module',
            body='<p>Accountants can now initiate E-Cash withdrawals directly from the Accounting dashboard. '
                 'This will reduce the need for manual tracking.</p>',
            post_type='SYSTEM_UPDATE',
            is_pinned=False,
            is_active=True,
        )
        bp4.target_roles.set([accountant_role])

        self.stdout.write(self.style.SUCCESS(f'    [DONE] {tenant.name}'))
