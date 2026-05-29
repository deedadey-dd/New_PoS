"""
Management command to set up the Demo Company environment with realistic mock data.

Covers:
- Two tenants: standard and strict workflow
- Locations (Production, Stores, 2 Shops per tenant)
- Full user roster with all roles
- Payment provider configs + shop payment assignments
- Products with batches and realistic stock levels
- FavoriteProduct entries
- ShopSettings (receipt, payment toggles)
- Customers with credit balances and transactions
- Mock Sales (CASH, ECASH, MOMO, CREDIT, MIXED), including PENDING queue for strict
- ECashLedger entries for ECASH/MOMO sales
- DigitalFundWithdrawal records
- CashTransfer + BankTransfer records
- StockAdjustments (PENDING and APPROVED)
- ExpenditureRequests with items
- Transfers (SENT/RECEIVED) and StockRequests
- RefundRequests (PENDING and APPROVED)
"""

import os
import random
from decimal import Decimal
from datetime import timedelta, date

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
from apps.payments.models import PaymentProviderConfig, ECashLedger, ShopPaymentAssignment

User = get_user_model()


class Command(BaseCommand):
    help = 'Sets up the Demo Company environment with realistic mock data covering all features.'

    def handle(self, *args, **kwargs):
        self.stdout.write('Starting Demo Environment Setup...')

        with transaction.atomic():
            # ----------------------------------------------------------------
            # 1. Clean existing demo data
            # ----------------------------------------------------------------
            self.stdout.write('Wiping old demo data...')
            tenants = Tenant.objects.filter(name__in=['Demo Company', 'Strict Demo Company'])
            for tenant in tenants:
                from apps.transfers.models import StockRequest, StockRequestItem, Transfer, TransferItem

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
                ECashLedger.objects.filter(tenant=tenant).delete()
                ShopPaymentAssignment.objects.filter(tenant=tenant).delete()
                PaymentProviderConfig.objects.filter(tenant=tenant).delete()

                # ShopSettings
                ShopSettings.objects.filter(tenant=tenant).delete()

                # Core
                Product.objects.filter(tenant=tenant).delete()
                Category.objects.filter(tenant=tenant).delete()
                Location.objects.filter(tenant=tenant).delete()
                User.objects.filter(tenant=tenant).delete()
                tenant.delete()

            User.objects.filter(email__endswith='@demo.com').delete()

            # ----------------------------------------------------------------
            # 2. Create Tenants
            # ----------------------------------------------------------------
            self.stdout.write('Creating Demo Tenants...')
            tenant_standard = Tenant.objects.create(
                name='Demo Company',
                currency='GHS',
                subscription_status='ACTIVE',
                phone='0241234567',
                allow_momo_payments=True,
                allow_inter_shop_transfers=True,
                enable_refunds=True,
                require_refund_approval=True,
                refund_always_cash=False,        # Mirror-payment refunds
                shop_manager_can_add_products=True,
                shop_manager_can_receive_stock=True,
                shops_can_see_other_stock=True,
                allow_accountant_to_shop_transfers=True,
                pricing_control_mode='SHOP_MANAGER',
                accountants_can_approve_adjustments=True,
                waive_shift_requirement=False,
            )
            tenant_strict = Tenant.objects.create(
                name='Strict Demo Company',
                currency='GHS',
                subscription_status='ACTIVE',
                phone='0241234568',
                use_strict_sales_workflow=True,
                use_cashier_workflow=True,
                allow_momo_payments=True,
                enable_refunds=True,
                require_refund_approval=True,
                refund_always_cash=True,          # Always cash refunds
                shop_manager_can_add_products=False,
                shop_manager_can_receive_stock=False,
                shops_can_see_other_stock=False,
                allow_accountant_to_shop_transfers=False,
                pricing_control_mode='ACCOUNTANT_PER_SHOP',
                accountants_can_approve_adjustments=False,
                cashier_transfers_to_bank=True,
                waive_shift_requirement=False,
            )

            # ----------------------------------------------------------------
            # 3. Ensure Roles exist
            # ----------------------------------------------------------------
            roles = {}
            role_names = [
                'ADMIN', 'AUDITOR', 'ACCOUNTANT',
                'PRODUCTION_MANAGER', 'STORES_MANAGER',
                'SHOP_MANAGER', 'SHOP_ATTENDANT', 'SHOP_CASHIER',
            ]
            for name in role_names:
                role, _ = Role.objects.get_or_create(
                    name=name, defaults={'description': f'Demo {name}'}
                )
                roles[name] = role

            for tenant, is_strict in [(tenant_standard, False), (tenant_strict, True)]:
                self._build_tenant_data(tenant, roles, is_strict)

        self.stdout.write(self.style.SUCCESS('Demo Environments successfully created!'))

    # ========================================================================
    # TENANT DATA BUILDER
    # ========================================================================

    def _build_tenant_data(self, tenant, roles, is_strict):
        prefix = 'strict_' if is_strict else ''
        now = timezone.now()

        # --------------------------------------------------------------------
        # 4. Locations
        # --------------------------------------------------------------------
        loc_production = Location.objects.create(
            tenant=tenant, name='Main Production', location_type='PRODUCTION', is_active=True
        )
        loc_stores = Location.objects.create(
            tenant=tenant, name='Central Warehouse', location_type='STORES', is_active=True
        )
        loc_shop1 = Location.objects.create(
            tenant=tenant, name='Downtown Shop', location_type='SHOP', is_active=True
        )
        loc_shop2 = Location.objects.create(
            tenant=tenant, name='Uptown Shop', location_type='SHOP', is_active=True
        )

        # --------------------------------------------------------------------
        # 5. Users
        # --------------------------------------------------------------------
        users_setup = [
            (f'{prefix}admin@demo.com',       'Admin',     'User',      roles['ADMIN'],              None),
            (f'{prefix}auditor@demo.com',      'System',    'Auditor',   roles['AUDITOR'],            None),
            (f'{prefix}accountant@demo.com',   'Chief',     'Accountant',roles['ACCOUNTANT'],         None),
            (f'{prefix}production@demo.com',   'Prod',      'Manager',   roles['PRODUCTION_MANAGER'], loc_production),
            (f'{prefix}stores@demo.com',       'Warehouse', 'Manager',   roles['STORES_MANAGER'],     loc_stores),
            (f'{prefix}manager1@demo.com',     'Downtown',  'Manager',   roles['SHOP_MANAGER'],       loc_shop1),
            (f'{prefix}attendant1@demo.com',   'Downtown',  'Attendant', roles['SHOP_ATTENDANT'],     loc_shop1),
            (f'{prefix}manager2@demo.com',     'Uptown',    'Manager',   roles['SHOP_MANAGER'],       loc_shop2),
            (f'{prefix}attendant2@demo.com',   'Uptown',    'Attendant', roles['SHOP_ATTENDANT'],     loc_shop2),
        ]
        if is_strict:
            users_setup.append(
                (f'cashier_strict@demo.com', 'Downtown', 'Cashier', roles['SHOP_CASHIER'], loc_shop1)
            )

        created_users = {}
        for email, fname, lname, role, loc in users_setup:
            u = User.objects.create_user(
                email=email, password='demo',
                first_name=fname, last_name=lname,
                tenant=tenant, role=role, location=loc, is_active=True,
            )
            created_users[email] = u

        attendant1 = created_users[f'{prefix}attendant1@demo.com']
        attendant2 = created_users[f'{prefix}attendant2@demo.com']
        manager1   = created_users[f'{prefix}manager1@demo.com']
        manager2   = created_users[f'{prefix}manager2@demo.com']
        accountant = created_users[f'{prefix}accountant@demo.com']
        stores_mgr = created_users[f'{prefix}stores@demo.com']
        prod_mgr   = created_users[f'{prefix}production@demo.com']
        cashier    = created_users.get('cashier_strict@demo.com')

        # --------------------------------------------------------------------
        # 6. Payment Provider Configs + Shop Assignments
        # --------------------------------------------------------------------
        paystack_config, _ = PaymentProviderConfig.objects.get_or_create(
            tenant=tenant, nickname='Main Paystack',
            defaults={'provider': 'PAYSTACK', 'is_active': True, 'public_key': 'demo_pk_paystack'}
        )
        nalopay_config, _ = PaymentProviderConfig.objects.get_or_create(
            tenant=tenant, nickname='Nalo Mobile',
            defaults={'provider': 'NALOPAY', 'is_active': True, 'public_key': 'demo_pk_nalo'}
        )
        appsnmobile_config, _ = PaymentProviderConfig.objects.get_or_create(
            tenant=tenant, nickname='AppsNMobile POS',
            defaults={'provider': 'APPSNMOBILE', 'is_active': True, 'public_key': 'demo_pk_anm'}
        )
        demo_providers = [paystack_config, nalopay_config, appsnmobile_config]

        # Assign providers to shops
        for shop in [loc_shop1, loc_shop2]:
            ShopPaymentAssignment.objects.get_or_create(
                tenant=tenant, shop=shop, provider_config=paystack_config,
                defaults={'is_default': True, 'priority': 0}
            )
            ShopPaymentAssignment.objects.get_or_create(
                tenant=tenant, shop=shop, provider_config=nalopay_config,
                defaults={'is_default': False, 'priority': 1}
            )

        # --------------------------------------------------------------------
        # 7. ShopSettings
        # --------------------------------------------------------------------
        for shop, mgr in [(loc_shop1, manager1), (loc_shop2, manager2)]:
            ShopSettings.objects.get_or_create(
                tenant=tenant, shop=shop,
                defaults={
                    'receipt_printer_type': 'THERMAL_80MM',
                    'auto_print_receipts': True,
                    'show_logo_on_receipt': True,
                    'enable_cash_payment': True,
                    'enable_credit_payment': True,
                    'enable_ecash_payment': True,
                    'enable_momo_payment': tenant.allow_momo_payments,
                    'hide_zero_stock_in_pos': False,
                    'receipt_header': f'Welcome to {tenant.name}',
                    'receipt_footer': 'Thank you for shopping with us!',
                }
            )

        # --------------------------------------------------------------------
        # 8. Product Categories + Products with Batches
        # --------------------------------------------------------------------
        cat_stationery = Category.objects.create(tenant=tenant, name='Stationery')
        cat_toys       = Category.objects.create(tenant=tenant, name='Toys')
        cat_snacks     = Category.objects.create(tenant=tenant, name='Snacks')
        cat_beverages  = Category.objects.create(
            tenant=tenant, name='Beverages', parent=cat_snacks
        )

        today = now.date()

        # (name, cat, default_price, cost, shop1_stock, shop2_stock, stores_stock, reorder_level, image, expiry_offset_days)
        demo_items = [
            # Well-stocked (GREEN)
            ('Blue Ink Pen',        cat_stationery, '2.50',  '1.20',  200, 150, 400, 20,  'blue_pen.png',          None),
            ('Fresh Cola 500ml',    cat_beverages,  '5.00',  '2.80',  120,  95, 300, 15,  'fresh_cola.png',        60),
            # Approaching low stock (YELLOW: qty < threshold * 2)
            ('Exercise Book 80pg',  cat_stationery, '5.00',  '2.50',   18,  25,  50, 10,  'exercise_book.png',     None),
            ('Action Figure Toy',   cat_toys,       '45.00', '20.00',  14,  20,  40,  8,  'action_toy.png',        None),
            # Critical stock (RED: qty <= threshold)
            ('Chocolate Biscuit',   cat_snacks,     '6.00',  '3.50',    3,   5,  20, 10,  'chocolate_biscuit.png', 45),
            # One product with two batches (to demo FEFO ordering)
            ('Bottled Water 500ml', cat_beverages,  '2.00',  '0.80',   80,  60, 200, 25,  None,                    30),
        ]

        products = []
        for name, cat, price, cost, stock1, stock2, stores_qty, reorder, user_image, expiry_offset in demo_items:
            sku = name.replace(' ', '').upper()[:8]
            p = Product.objects.create(
                tenant=tenant, category=cat, name=name, sku=sku,
                default_selling_price=Decimal(price),
                is_active=True,
                reorder_level=Decimal(str(reorder)),
            )

            # Attach demo image if available
            if user_image and settings.STATICFILES_DIRS:
                img_path = os.path.join(settings.STATICFILES_DIRS[0], 'images', 'demo', user_image)
                if os.path.exists(img_path):
                    with open(img_path, 'rb') as f:
                        p.image.save(user_image, File(f), save=True)

            expiry_date = (today + timedelta(days=expiry_offset)) if expiry_offset else None

            # Create a primary batch at Stores
            batch_stores = Batch.objects.create(
                tenant=tenant,
                product=p,
                location=loc_stores,
                batch_number=f'BATCH-{sku}-001',
                unit_cost=Decimal(cost),
                initial_quantity=Decimal(str(stores_qty)),
                current_quantity=Decimal(str(stores_qty)),
                received_date=today - timedelta(days=random.randint(5, 30)),
                expiry_date=expiry_date,
                status='AVAILABLE',
            )

            # For the water product, add a second (older, cheaper) batch to demo FEFO
            batch_stores_old = None
            if name == 'Bottled Water 500ml':
                old_expiry = (today + timedelta(days=10)) if expiry_offset else None
                batch_stores_old = Batch.objects.create(
                    tenant=tenant,
                    product=p,
                    location=loc_stores,
                    batch_number=f'BATCH-{sku}-000',
                    unit_cost=Decimal('0.70'),
                    initial_quantity=Decimal('50'),
                    current_quantity=Decimal('50'),
                    received_date=today - timedelta(days=60),
                    expiry_date=old_expiry,
                    status='AVAILABLE',
                )

            # Create shop batches (transferred from stores)
            batch_shop1 = Batch.objects.create(
                tenant=tenant, product=p, location=loc_shop1,
                batch_number=f'BATCH-{sku}-001',
                unit_cost=Decimal(cost),
                initial_quantity=Decimal(str(stock1)),
                current_quantity=Decimal(str(stock1)),
                received_date=today - timedelta(days=random.randint(1, 10)),
                expiry_date=expiry_date,
                status='AVAILABLE',
            )
            batch_shop2 = Batch.objects.create(
                tenant=tenant, product=p, location=loc_shop2,
                batch_number=f'BATCH-{sku}-001',
                unit_cost=Decimal(cost),
                initial_quantity=Decimal(str(stock2)),
                current_quantity=Decimal(str(stock2)),
                received_date=today - timedelta(days=random.randint(1, 10)),
                expiry_date=expiry_date,
                status='AVAILABLE',
            )

            # Production batch (raw materials / finished goods)
            prod_qty = stock1 * 2
            batch_prod = Batch.objects.create(
                tenant=tenant, product=p, location=loc_production,
                batch_number=f'BATCH-{sku}-001',
                unit_cost=Decimal(cost),
                initial_quantity=Decimal(str(prod_qty)),
                current_quantity=Decimal(str(prod_qty)),
                received_date=today - timedelta(days=random.randint(10, 45)),
                expiry_date=expiry_date,
                status='AVAILABLE',
            )

            products.append({
                'product': p,
                'stock1': stock1, 'stock2': stock2,
                'stores_qty': stores_qty,
                'batch_shop1': batch_shop1, 'batch_shop2': batch_shop2,
                'batch_stores': batch_stores,
            })

        # --------------------------------------------------------------------
        # 9. Inventory Ledger Snapshots + ShopPrices
        # --------------------------------------------------------------------
        for item in products:
            p = item['product']
            for shop, qty, batch in [
                (loc_shop1, item['stock1'], item['batch_shop1']),
                (loc_shop2, item['stock2'], item['batch_shop2']),
            ]:
                InventoryLedger.objects.create(
                    tenant=tenant, product=p, batch=batch,
                    location=shop, transaction_type='IN',
                    quantity=Decimal(str(qty)),
                    unit_cost=batch.unit_cost,
                    reference_type=f'DEMO-SEED-{p.pk}',
                    notes='Initial demo stock',
                )
                Inventory.objects.create(
                    tenant=tenant, location=shop, product=p, quantity=Decimal(str(qty))
                )
                ShopPrice.objects.create(
                    tenant=tenant, location=shop, product=p,
                    selling_price=p.default_selling_price,
                )

            for loc, qty, batch in [
                (loc_stores,     item['stores_qty'],    item['batch_stores']),
                (loc_production, item['stock1'] * 2,    item['batch_shop1']),  # re-use cost
            ]:
                InventoryLedger.objects.create(
                    tenant=tenant, product=p, batch=batch,
                    location=loc, transaction_type='IN',
                    quantity=Decimal(str(qty)),
                    unit_cost=batch.unit_cost,
                    reference_type=f'DEMO-SEED-{p.pk}',
                    notes='Initial demo stock',
                )
                Inventory.objects.create(
                    tenant=tenant, location=loc, product=p, quantity=Decimal(str(qty))
                )

        # --------------------------------------------------------------------
        # 10. FavoriteProducts (top-sellers at each shop)
        # --------------------------------------------------------------------
        favorite_names = ['Blue Ink Pen', 'Fresh Cola 500ml', 'Chocolate Biscuit']
        for item in products:
            if item['product'].name in favorite_names:
                for shop, mgr in [(loc_shop1, manager1), (loc_shop2, manager2)]:
                    FavoriteProduct.objects.get_or_create(
                        tenant=tenant, location=shop, product=item['product'],
                        defaults={'created_by': mgr}
                    )

        # --------------------------------------------------------------------
        # 11. Customers
        # --------------------------------------------------------------------
        demo_customers = [
            ('Kwame Asante',  '0241111111', loc_shop1, Decimal('120.00'), Decimal('500.00')),
            ('Ama Serwah',    '0242222222', loc_shop1, Decimal('45.50'),  Decimal('200.00')),
            ('Kofi Mensah',   '0243333333', loc_shop1, Decimal('0.00'),   Decimal('300.00')),
            ('Akua Boateng',  '0244444444', loc_shop2, Decimal('230.00'), Decimal('500.00')),
            ('Yaw Darko',     '0245555555', loc_shop2, Decimal('15.00'),  Decimal('100.00')),
            ('Efua Nyarko',   '0246666666', loc_shop2, Decimal('0.00'),   Decimal('150.00')),
        ]
        customer_map = {}
        for cname, cphone, cshop, balance, limit in demo_customers:
            attendant = attendant1 if cshop == loc_shop1 else attendant2
            customer = Customer.objects.create(
                tenant=tenant, name=cname, phone=cphone,
                shop=cshop, current_balance=balance,
                credit_limit=limit, is_active=True,
            )
            customer_map[(cname, cshop.pk)] = customer
            if balance > 0:
                CustomerTransaction.objects.create(
                    tenant=tenant, customer=customer,
                    transaction_type='DEBIT', amount=balance,
                    description='Credit purchase (demo seed)',
                    balance_before=Decimal('0.00'), balance_after=balance,
                    performed_by=attendant,
                )

        # --------------------------------------------------------------------
        # 12. Shift (Downtown Shop)
        # --------------------------------------------------------------------
        shift = Shift.objects.create(
            tenant=tenant, shop=loc_shop1, attendant=attendant1,
            start_time=now - timedelta(hours=8),
            status='OPEN', opening_cash=Decimal('50.00'),
        )

        # --------------------------------------------------------------------
        # 13. Mock Sales – CASH, ECASH, MOMO, CREDIT, MIXED
        # --------------------------------------------------------------------
        payment_pool = ['CASH', 'CASH', 'CASH', 'ECASH', 'MOMO', 'CREDIT', 'MIXED']

        completed_sales = []   # collect for refund seeding

        for i in range(30):
            days_ago  = random.randint(0, 20)
            sale_time = now - timedelta(days=days_ago, hours=random.randint(1, 10))
            shop      = random.choice([loc_shop1, loc_shop2])
            attendant = attendant1 if shop == loc_shop1 else attendant2

            payment_method = random.choice(payment_pool)

            # Strict workflow: today's Downtown sales may be PENDING
            status  = 'COMPLETED'
            _cashier = None
            if is_strict and days_ago == 0 and shop == loc_shop1:
                status = random.choice(['PENDING', 'PENDING_DISPATCH', 'COMPLETED'])
                if status in ('COMPLETED', 'PENDING_DISPATCH'):
                    _cashier = cashier

            # Credit sale needs a customer
            sale_customer = None
            if payment_method in ('CREDIT', 'MIXED'):
                shop_customers = Customer.objects.filter(tenant=tenant, shop=shop, is_active=True)
                if shop_customers.exists():
                    sale_customer = random.choice(list(shop_customers))
                else:
                    payment_method = 'CASH'

            sale = Sale.objects.create(
                tenant=tenant, shop=shop, attendant=attendant,
                cashier=_cashier,
                shift=shift if shop == loc_shop1 else None,
                status=status,
                payment_method=payment_method,
                amount_paid=Decimal('0'),
                sale_number=f'DEMO-SL-{i:04d}-{random.randint(100,999)}',
                customer=sale_customer,
                dispatched_by=attendant if status == 'COMPLETED' else None,
                dispatched_at=sale_time if status == 'COMPLETED' else None,
                is_dispatched=(status == 'COMPLETED'),
            )
            Sale.objects.filter(pk=sale.pk).update(created_at=sale_time)

            # Add items
            sale_total = Decimal('0')
            for _ in range(random.randint(1, 4)):
                prod_item = random.choice(products)
                p  = prod_item['product']
                qty = Decimal(str(random.randint(1, 5)))
                unit_price = p.default_selling_price
                item_total = unit_price * qty
                SaleItem.objects.create(
                    tenant=tenant, sale=sale, product=p,
                    quantity=qty, unit_price=unit_price,
                    unit_cost=prod_item['batch_shop1'].unit_cost,
                    total=item_total,
                )
                sale_total += item_total

            sale.total    = sale_total
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

            # Seed ECashLedger for ECASH / MOMO
            if payment_method in ('ECASH', 'MOMO') and status == 'COMPLETED':
                provider_config = random.choice(demo_providers)
                ECashLedger.objects.create(
                    tenant=tenant, shop=shop,
                    transaction_type='PAYMENT',
                    amount=sale_total,
                    reference_type='Sale', reference_id=sale.pk,
                    provider_config=provider_config,
                    provider=provider_config.provider,
                    created_by=attendant,
                    notes=f'Demo {payment_method} Sale',
                )
                ECashLedger.objects.filter(
                    reference_type='Sale', reference_id=sale.pk
                ).update(created_at=sale_time)

            if status == 'COMPLETED':
                completed_sales.append(sale)

        # --------------------------------------------------------------------
        # 14. PENDING sales for Cashier Queue (strict workflow)
        # --------------------------------------------------------------------
        if is_strict:
            for i in range(4):
                pending_sale = Sale.objects.create(
                    tenant=tenant, shop=loc_shop1, attendant=attendant1,
                    shift=shift, status='PENDING',
                    payment_method=random.choice(['CASH', 'MOMO']),
                    amount_paid=Decimal('0'),
                    sale_number=f'DEMO-SL-P{random.randint(100, 999)}',
                )
                p_total = Decimal('0')
                for _ in range(2):
                    prod_item = random.choice(products)
                    p   = prod_item['product']
                    qty = Decimal(str(random.randint(1, 3)))
                    it  = p.default_selling_price * qty
                    SaleItem.objects.create(
                        tenant=tenant, sale=pending_sale, product=p,
                        quantity=qty, unit_price=p.default_selling_price,
                        unit_cost=prod_item['batch_shop1'].unit_cost, total=it,
                    )
                    p_total += it
                pending_sale.total    = p_total
                pending_sale.subtotal = p_total
                pending_sale.save()

        # --------------------------------------------------------------------
        # 15. RefundRequests (requires enable_refunds=True)
        # --------------------------------------------------------------------
        refundable = [s for s in completed_sales if s.status == 'COMPLETED']
        if refundable:
            # One PENDING refund request
            pending_sale = random.choice(refundable)
            RefundRequest.objects.create(
                tenant=tenant,
                sale=pending_sale,
                requested_by=manager1 if pending_sale.shop == loc_shop1 else manager2,
                reason='Customer returned item — wrong size.',
                status='PENDING',
            )
            # One already-approved refund (if enough sales)
            if len(refundable) > 1:
                approved_sale = random.choice([s for s in refundable if s != pending_sale])
                rr = RefundRequest.objects.create(
                    tenant=tenant,
                    sale=approved_sale,
                    requested_by=manager1 if approved_sale.shop == loc_shop1 else manager2,
                    reason='Damaged goods returned by customer.',
                    status='APPROVED',
                    reviewed_by=accountant,
                    reviewed_at=now - timedelta(days=2),
                )

        # --------------------------------------------------------------------
        # 16. CashTransfers (shop → accountant)
        # --------------------------------------------------------------------
        for _ in range(6):
            days_ago      = random.randint(1, 20)
            transfer_time = now - timedelta(days=days_ago)
            shop = random.choice([loc_shop1, loc_shop2])
            mgr  = manager1 if shop == loc_shop1 else manager2
            ct = CashTransfer.objects.create(
                tenant=tenant,
                from_location=shop, from_user=mgr,
                to_user=accountant if not (is_strict and random.choice([True, False])) else (cashier or accountant),
                transfer_type='DEPOSIT',
                amount=Decimal(str(random.randint(50, 500))),
                status='CONFIRMED',
                confirmed_at=transfer_time,
                confirmed_by=accountant,
            )
            CashTransfer.objects.filter(pk=ct.pk).update(created_at=transfer_time)

        # --------------------------------------------------------------------
        # 17. BankTransfers (accountant deposits to bank)
        # --------------------------------------------------------------------
        for _ in range(4):
            days_ago      = random.randint(1, 20)
            transfer_time = now - timedelta(days=days_ago)
            fund_source   = random.choice(['CASH', 'ECASH', 'MOMO'])
            provider_config = random.choice(demo_providers) if fund_source in ('ECASH', 'MOMO') else None

            bt = BankTransfer.objects.create(
                tenant=tenant,
                accountant=accountant,
                amount=Decimal(str(random.randint(100, 1000))),
                fund_source=fund_source,
                provider_config=provider_config,
                teller_name=f'Teller {random.randint(1, 5)}',
                notes='Daily bank deposit',
            )
            BankTransfer.objects.filter(pk=bt.pk).update(created_at=transfer_time)

        # --------------------------------------------------------------------
        # 18. DigitalFundWithdrawals (accountant pulls e-cash / momo)
        # --------------------------------------------------------------------
        for _ in range(3):
            days_ago      = random.randint(1, 15)
            transfer_time = now - timedelta(days=days_ago)
            shop          = random.choice([loc_shop1, loc_shop2])
            fund_source   = random.choice(['ECASH', 'MOMO'])
            provider_config = random.choice(demo_providers)

            dfw = DigitalFundWithdrawal.objects.create(
                tenant=tenant,
                shop=shop,
                accountant=accountant,
                amount=Decimal(str(random.randint(50, 300))),
                fund_source=fund_source,
                provider_config=provider_config,
                notes=f'Demo {fund_source} withdrawal from {shop.name}',
            )
            DigitalFundWithdrawal.objects.filter(pk=dfw.pk).update(created_at=transfer_time)

        # --------------------------------------------------------------------
        # 19. StockAdjustments (APPROVED + PENDING)
        # --------------------------------------------------------------------
        adj_product = products[0]['product']  # Blue Ink Pen
        adj_batch   = products[0]['batch_shop1']

        # An approved positive adjustment (stock top-up)
        adj_approved = StockAdjustment.objects.create(
            tenant=tenant, product=adj_product, batch=adj_batch,
            location=loc_shop1, adjustment_type='ADJUST',
            quantity=Decimal('10'), reason='Cycle count correction',
            status='APPROVED', requested_by=manager1,
            reviewed_by=accountant, reviewed_at=now - timedelta(days=3),
        )

        # A pending damage write-off
        dmg_product = products[4]['product']  # Chocolate Biscuit
        StockAdjustment.objects.create(
            tenant=tenant, product=dmg_product,
            batch=products[4]['batch_shop1'],
            location=loc_shop1, adjustment_type='DAMAGE',
            quantity=Decimal('-2'), reason='Water damage in storage',
            status='PENDING', requested_by=manager1,
        )

        # --------------------------------------------------------------------
        # 20. ExpenditureRequests
        # --------------------------------------------------------------------
        cat_office, _ = ExpenditureCategory.objects.get_or_create(
            tenant=tenant, name='Office Supplies', defaults={'is_default': True}
        )
        cat_maint, _ = ExpenditureCategory.objects.get_or_create(
            tenant=tenant, name='Maintenance', defaults={'is_default': True}
        )
        cat_transport, _ = ExpenditureCategory.objects.get_or_create(
            tenant=tenant, name='Transportation', defaults={'is_default': True}
        )

        # Use explicit voucher numbers to avoid the race between auto-gen
        # (which counts created_at__date=today) and our backdating update.
        # Format: DEMO-<tenant_pk>-EXP-<nn>  — guaranteed unique across both tenants.
        tenant_tag = f'DEMO-{tenant.pk}'
        for exp_idx in range(6):
            days_ago = random.randint(0, 20)
            exp_time = now - timedelta(days=days_ago, hours=random.randint(1, 10))
            shop     = random.choice([loc_shop1, loc_shop2])
            mgr      = manager1 if shop == loc_shop1 else manager2
            source   = 'SHOP_CASH' if is_strict else random.choice(['SHOP_CASH', 'ACCOUNTANT'])

            voucher_number = f'{tenant_tag}-EXP-{exp_idx + 1:04d}'
            req = ExpenditureRequest.objects.create(
                tenant=tenant, location=shop,
                requested_by=mgr, status='FULLY_APPROVED',
                voucher_number=voucher_number,
            )
            ExpenditureRequest.objects.filter(pk=req.pk).update(created_at=exp_time)

            ExpenditureItem.objects.create(
                tenant=tenant, request=req,
                category=random.choice([cat_office, cat_maint, cat_transport]),
                description='Demo expenditure item',
                amount=Decimal(str(random.randint(20, 200))),
                status='APPROVED',
                approved_by=accountant, approved_at=exp_time,
                source_of_funds=source,
            )

        # --------------------------------------------------------------------
        # 21. Stock Requests + Transfers (Stores → Shop1 and Production → Stores)
        # --------------------------------------------------------------------
        from apps.transfers.models import StockRequest, StockRequestItem, Transfer, TransferItem

        # Stock Request: Shop1 requests from Stores
        req = StockRequest.objects.create(
            tenant=tenant,
            requesting_location=loc_shop1,
            supplying_location=loc_stores,
            status='CONVERTED',
            requested_by=manager1,
            approved_by=stores_mgr,
            approved_at=now - timedelta(days=5),
            notes='Restocking ahead of busy period',
        )
        req_item_product = products[0]['product']
        sri = StockRequestItem.objects.create(
            tenant=tenant, request=req, product=req_item_product,
            quantity_requested=Decimal('50'),
        )

        # Transfer: Stores → Shop1 (RECEIVED)
        transfer = Transfer.objects.create(
            tenant=tenant,
            source_location=loc_stores,
            destination_location=loc_shop1,
            status='RECEIVED',
            created_by=stores_mgr,
            sent_by=stores_mgr, sent_at=now - timedelta(days=4),
            received_by=manager1, received_at=now - timedelta(days=3),
            notes='Fulfilling stock request',
        )
        req.resulting_transfer = transfer
        req.save(update_fields=['resulting_transfer'])

        TransferItem.objects.create(
            tenant=tenant, transfer=transfer,
            product=req_item_product,
            batch=products[0]['batch_stores'],
            quantity_requested=Decimal('50'),
            quantity_sent=Decimal('50'),
            quantity_received=Decimal('50'),
            unit_cost=products[0]['batch_stores'].unit_cost,
        )

        # Transfer: Production → Stores (SENT — in-transit)
        transfer2 = Transfer.objects.create(
            tenant=tenant,
            source_location=loc_production,
            destination_location=loc_stores,
            status='SENT',
            created_by=prod_mgr,
            sent_by=prod_mgr, sent_at=now - timedelta(hours=6),
            notes='Weekly production output',
        )
        bev_item = next((i for i in products if 'Cola' in i['product'].name), products[1])
        TransferItem.objects.create(
            tenant=tenant, transfer=transfer2,
            product=bev_item['product'],
            batch=bev_item['batch_stores'],
            quantity_requested=Decimal('100'),
            quantity_sent=Decimal('100'),
            quantity_received=Decimal('0'),
            unit_cost=bev_item['batch_stores'].unit_cost,
        )

        # A pending stock request (no transfer yet)
        pending_req = StockRequest.objects.create(
            tenant=tenant,
            requesting_location=loc_shop2,
            supplying_location=loc_stores,
            status='PENDING',
            requested_by=manager2,
            notes='Running low on Chocolate Biscuits',
        )
        StockRequestItem.objects.create(
            tenant=tenant, request=pending_req,
            product=products[4]['product'],
            quantity_requested=Decimal('30'),
        )

        self.stdout.write(
            self.style.SUCCESS(f"  ✓ Built data for: {tenant.name}")
        )
