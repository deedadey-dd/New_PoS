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
from apps.inventory.models import Category, Product, Batch, Inventory, InventoryLedger, ShopPrice, GoodsReceipt, GoodsReceiptItem
from apps.transfers.models import Transfer, TransferItem
from apps.sales.models import Sale, SaleItem, Shift
from apps.accounting.models import CashTransfer
from apps.customers.models import Customer, CustomerTransaction

User = get_user_model()

class Command(BaseCommand):
    help = 'Sets up the Demo Company environment with realistic mock data.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting Demo Environment Setup...")

        with transaction.atomic():
            self._setup_standard_demo()
            self._setup_strict_demo()

        self.stdout.write(self.style.SUCCESS("Demo Environment successfully created!"))

    # ─────────────────────────────────────────────────────────────
    # Standard Demo
    # ─────────────────────────────────────────────────────────────
    def _setup_standard_demo(self):
        self.stdout.write("Setting up Standard Demo (Demo Company)...")

        # 1. Clean existing demo data
        self.stdout.write("  Wiping old standard demo data...")
        tenants = Tenant.objects.filter(name="Demo Company")
        if tenants.exists():
            tenant = tenants.first()
            self._wipe_tenant_data(tenant)
        User.objects.filter(email__endswith='@demo.com').delete()

        # 2. Setup Tenant
        tenant = Tenant.objects.create(
            name="Demo Company",
            currency="GHS",
            subscription_status="ACTIVE",
            phone="0241234567",
            # Standard settings: all defaults (strict workflow OFF)
        )

        roles, loc_production, loc_stores, loc_shop1, loc_shop2, products, created_users = \
            self._create_base_data(tenant, email_suffix='@demo.com')

        # 9. Mock sales & transfers
        self._create_mock_sales(tenant, loc_shop1, loc_shop2, products, created_users)
        self._create_mock_cash_transfers(tenant, loc_shop1, loc_shop2, created_users)

    # ─────────────────────────────────────────────────────────────
    # Strict Demo
    # ─────────────────────────────────────────────────────────────
    def _setup_strict_demo(self):
        self.stdout.write("Setting up Strict Demo (Demo Company - Strict Workflow)...")

        # 1. Clean existing strict demo data
        self.stdout.write("  Wiping old strict demo data...")
        tenants = Tenant.objects.filter(name="Demo Company (Strict Workflow)")
        if tenants.exists():
            tenant = tenants.first()
            self._wipe_tenant_data(tenant)
        User.objects.filter(email__endswith='@strict-demo.com').delete()

        # 2. Setup Tenant with strict settings
        tenant = Tenant.objects.create(
            name="Demo Company (Strict Workflow)",
            currency="GHS",
            subscription_status="ACTIVE",
            phone="0241234567",
            use_strict_sales_workflow=True,
            require_customer_on_invoice=True,
            use_bulk_inventory_receiving=True,
            shop_manager_can_receive_stock=True,
            require_accountant_for_bulk_receiving=False,
        )

        roles, loc_production, loc_stores, loc_shop1, loc_shop2, products, created_users = \
            self._create_base_data(tenant, email_suffix='@strict-demo.com')

        # Mock sales & transfers
        self._create_mock_sales(tenant, loc_shop1, loc_shop2, products, created_users)
        self._create_mock_cash_transfers(tenant, loc_shop1, loc_shop2, created_users)

    # ─────────────────────────────────────────────────────────────
    # Shared helpers
    # ─────────────────────────────────────────────────────────────
    def _wipe_tenant_data(self, tenant):
        """Delete all related data for a tenant in the correct dependency order."""
        GoodsReceiptItem.objects.filter(tenant=tenant).delete()
        GoodsReceipt.objects.filter(tenant=tenant).delete()
        TransferItem.objects.filter(transfer__tenant=tenant).delete()
        Transfer.objects.filter(tenant=tenant).delete()
        CustomerTransaction.objects.filter(tenant=tenant).delete()
        Customer.objects.filter(tenant=tenant).delete()
        SaleItem.objects.filter(tenant=tenant).delete()
        Sale.objects.filter(tenant=tenant).delete()
        CashTransfer.objects.filter(tenant=tenant).delete()
        Shift.objects.filter(tenant=tenant).delete()
        InventoryLedger.objects.filter(tenant=tenant).delete()
        Inventory.objects.filter(tenant=tenant).delete()
        Batch.objects.filter(tenant=tenant).delete()
        ShopPrice.objects.filter(tenant=tenant).delete()
        Product.objects.filter(tenant=tenant).delete()
        Category.objects.filter(tenant=tenant).delete()
        Location.objects.filter(tenant=tenant).delete()
        User.objects.filter(tenant=tenant).delete()
        tenant.delete()

    def _create_base_data(self, tenant, email_suffix):
        """Create roles, locations, users, products, customers for a given tenant."""

        # Roles
        roles = {}
        role_names = ['ADMIN', 'AUDITOR', 'ACCOUNTANT', 'PRODUCTION_MANAGER',
                      'STORES_MANAGER', 'SHOP_MANAGER', 'SHOP_ATTENDANT', 'SHOP_CASHIER']
        for name in role_names:
            role, _ = Role.objects.get_or_create(name=name, defaults={'description': f'Demo {name}'})
            roles[name] = role

        # Locations
        loc_production = Location.objects.create(tenant=tenant, name="Main Production", location_type="PRODUCTION", is_active=True)
        loc_stores = Location.objects.create(tenant=tenant, name="Central Warehouse", location_type="STORES", is_active=True)
        loc_shop1 = Location.objects.create(tenant=tenant, name="Downtown Shop", location_type="SHOP", is_active=True)
        loc_shop2 = Location.objects.create(tenant=tenant, name="Uptown Shop", location_type="SHOP", is_active=True)

        # Users
        users_setup = [
            (f'admin{email_suffix}',       'Admin',     'User',     roles['ADMIN'],               None),
            (f'auditor{email_suffix}',     'System',    'Auditor',  roles['AUDITOR'],              None),
            (f'accountant{email_suffix}',  'Chief',     'Accountant', roles['ACCOUNTANT'],         None),
            (f'production{email_suffix}',  'Prod',      'Manager',  roles['PRODUCTION_MANAGER'],   loc_production),
            (f'stores{email_suffix}',      'Warehouse', 'Manager',  roles['STORES_MANAGER'],       loc_stores),
            (f'manager1{email_suffix}',    'Downtown',  'Manager',  roles['SHOP_MANAGER'],         loc_shop1),
            (f'attendant1{email_suffix}',  'Downtown',  'Attendant', roles['SHOP_ATTENDANT'],      loc_shop1),
            (f'manager2{email_suffix}',    'Uptown',    'Manager',  roles['SHOP_MANAGER'],         loc_shop2),
            (f'attendant2{email_suffix}',  'Uptown',    'Attendant', roles['SHOP_ATTENDANT'],      loc_shop2),
        ]
        # Add cashier only for strict demo (where SHOP_CASHIER role exists in the map)
        if 'SHOP_CASHIER' in roles:
            users_setup.append((f'cashier1{email_suffix}', 'Downtown', 'Cashier', roles['SHOP_CASHIER'], loc_shop1))

        created_users = {}
        for email, fname, lname, role, loc in users_setup:
            u = User.objects.create_user(
                email=email, password='demo',
                first_name=fname, last_name=lname,
                tenant=tenant, role=role, location=loc, is_active=True
            )
            created_users[email] = u

        # Products
        cat_stationery = Category.objects.create(tenant=tenant, name="Stationery")
        cat_toys = Category.objects.create(tenant=tenant, name="Toys")
        cat_snacks = Category.objects.create(tenant=tenant, name="Snacks")

        demo_items = [
            ("Blue Ink Pen",          cat_stationery, '2.50',  200, 150, 20, 'blue_pen.png'),
            ("Fresh Cola 500ml",      cat_snacks,     '5.00',  120,  95, 15, 'fresh_cola.png'),
            ("Exercise Book (80pg)",  cat_stationery, '5.00',   18,  25, 10, 'exercise_book.png'),
            ("Action Figure Toy",     cat_toys,       '45.00',  14,  20,  8, 'action_toy.png'),
            ("Chocolate Biscuit",     cat_snacks,     '6.00',    3,   5, 10, 'chocolate_biscuit.png'),
        ]

        products = []
        for name, cat, price, stock1, stock2, reorder, user_image in demo_items:
            p = Product.objects.create(
                tenant=tenant, category=cat, name=name,
                sku=name.replace(" ", "").upper()[:8],
                default_selling_price=Decimal(price), is_active=True,
                reorder_level=Decimal(str(reorder))
            )
            img_path = os.path.join(settings.STATICFILES_DIRS[0], 'images', 'demo', user_image)
            if os.path.exists(img_path):
                with open(img_path, 'rb') as f:
                    p.image.save(user_image, File(f), save=True)
            products.append((p, stock1, stock2))

        # Inventory
        for p, stock1, stock2 in products:
            for shop, stock_qty in [(loc_shop1, stock1), (loc_shop2, stock2)]:
                InventoryLedger.objects.create(
                    tenant=tenant, product=p, location=shop,
                    transaction_type='IN', quantity=Decimal(str(stock_qty)),
                    reference_type=f'DEMO-SEED-{p.pk}', notes='Initial demo stock'
                )
                Inventory.objects.create(tenant=tenant, location=shop, product=p, quantity=Decimal(str(stock_qty)))
                ShopPrice.objects.create(tenant=tenant, location=shop, product=p, selling_price=p.default_selling_price)

            for loc, qty in [(loc_stores, stock1 + stock2), (loc_production, stock1 * 2)]:
                InventoryLedger.objects.create(
                    tenant=tenant, product=p, location=loc,
                    transaction_type='IN', quantity=Decimal(str(qty)),
                    reference_type=f'DEMO-SEED-{p.pk}', notes='Initial demo stock'
                )
                Inventory.objects.create(tenant=tenant, location=loc, product=p, quantity=Decimal(str(qty)))

        # Customers
        attendant1_key = f'attendant1{email_suffix}'
        attendant2_key = f'attendant2{email_suffix}'
        demo_customers = [
            ("Kwame Asante",  "0241111111", loc_shop1, Decimal('120.00'), Decimal('500.00')),
            ("Ama Serwah",    "0242222222", loc_shop1, Decimal('45.50'),  Decimal('200.00')),
            ("Kofi Mensah",   "0243333333", loc_shop1, Decimal('0.00'),   Decimal('300.00')),
            ("Akua Boateng",  "0244444444", loc_shop2, Decimal('230.00'), Decimal('500.00')),
            ("Yaw Darko",     "0245555555", loc_shop2, Decimal('15.00'),  Decimal('100.00')),
            ("Efua Nyarko",   "0246666666", loc_shop2, Decimal('0.00'),   Decimal('150.00')),
        ]
        for cname, cphone, cshop, balance, limit in demo_customers:
            customer = Customer.objects.create(
                tenant=tenant, name=cname, phone=cphone, shop=cshop,
                current_balance=balance, credit_limit=limit, is_active=True
            )
            if balance > 0:
                att = created_users[attendant1_key if cshop == loc_shop1 else attendant2_key]
                CustomerTransaction.objects.create(
                    tenant=tenant, customer=customer, transaction_type='DEBIT',
                    amount=balance, description='Credit purchase (demo data)',
                    balance_before=Decimal('0.00'), balance_after=balance, performed_by=att
                )

        return roles, loc_production, loc_stores, loc_shop1, loc_shop2, products, created_users

    def _create_mock_sales(self, tenant, loc_shop1, loc_shop2, products, created_users):
        """Create 25 random sales spread over the last 15 days."""
        now = timezone.now()
        attendant1_key = [k for k in created_users if 'attendant1' in k][0]
        attendant2_key = [k for k in created_users if 'attendant2' in k][0]

        shift = Shift.objects.create(
            tenant=tenant, shop=loc_shop1,
            attendant=created_users[attendant1_key],
            start_time=now - timedelta(hours=8),
            status='OPEN', opening_cash=Decimal('50.00'),
        )

        for i in range(25):
            days_ago = random.randint(0, 15)
            sale_time = now - timedelta(days=days_ago, hours=random.randint(1, 10))
            shop = random.choice([loc_shop1, loc_shop2])
            attendant = created_users[attendant1_key if shop == loc_shop1 else attendant2_key]
            payment_method = random.choice(['CASH', 'CASH', 'ECASH', 'CREDIT'])

            sale_customer = None
            if payment_method == 'CREDIT':
                shop_customers = Customer.objects.filter(tenant=tenant, shop=shop, is_active=True)
                if shop_customers.exists():
                    sale_customer = random.choice(list(shop_customers))

            sale = Sale.objects.create(
                tenant=tenant, shop=shop, attendant=attendant,
                shift=shift if shop == loc_shop1 else None,
                status='COMPLETED', payment_method=payment_method,
                amount_paid=Decimal('0'),
                sale_number=f"DEMO-SL-{random.randint(10000, 99999)}",
                customer=sale_customer
            )
            Sale.objects.filter(pk=sale.pk).update(created_at=sale_time)

            sale_total = Decimal('0')
            for _ in range(random.randint(1, 4)):
                p, _s1, _s2 = random.choice(products)
                qty = Decimal(str(random.randint(1, 5)))
                item_total = p.default_selling_price * qty
                SaleItem.objects.create(
                    tenant=tenant, sale=sale, product=p, quantity=qty,
                    unit_price=p.default_selling_price, total=item_total
                )
                sale_total += item_total

            sale.total = sale_total
            sale.subtotal = sale_total
            sale.amount_paid = sale_total if payment_method in ['CASH', 'ECASH'] else Decimal('0')
            sale.save()

    def _create_mock_cash_transfers(self, tenant, loc_shop1, loc_shop2, created_users):
        """Create 5 confirmed cash transfers."""
        now = timezone.now()
        manager1_key = [k for k in created_users if 'manager1' in k][0]
        manager2_key = [k for k in created_users if 'manager2' in k][0]
        accountant_key = [k for k in created_users if 'accountant' in k][0]

        for _ in range(5):
            days_ago = random.randint(1, 15)
            transfer_time = now - timedelta(days=days_ago)
            shop = random.choice([loc_shop1, loc_shop2])
            manager = created_users[manager1_key if shop == loc_shop1 else manager2_key]
            ct = CashTransfer.objects.create(
                tenant=tenant, from_location=shop, from_user=manager,
                to_user=created_users[accountant_key],
                transfer_type='DEPOSIT',
                amount=Decimal(str(random.randint(50, 200))),
                status='CONFIRMED', confirmed_at=transfer_time
            )
            CashTransfer.objects.filter(pk=ct.pk).update(created_at=transfer_time)
