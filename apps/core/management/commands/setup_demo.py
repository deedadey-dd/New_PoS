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
from apps.inventory.models import Category, Product, Batch, Inventory, InventoryLedger, ShopPrice
from apps.sales.models import Sale, SaleItem, Shift
from apps.accounting.models import CashTransfer, BankTransfer
from apps.customers.models import Customer, CustomerTransaction
from apps.payments.models import PaymentProviderConfig, ECashLedger

User = get_user_model()

class Command(BaseCommand):
    help = 'Sets up the Demo Company environment with realistic mock data.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting Demo Environment Setup...")

        with transaction.atomic():
            # 1. Clean existing demo data
            self.stdout.write("Wiping old demo data...")
            tenants = Tenant.objects.filter(name__in=["Demo Company", "Strict Demo Company"])
            for tenant in tenants:
                from apps.transfers.models import StockRequest, StockRequestItem, Transfer, TransferItem
                TransferItem.objects.filter(tenant=tenant).delete()
                Transfer.objects.filter(tenant=tenant).delete()
                StockRequestItem.objects.filter(tenant=tenant).delete()
                StockRequest.objects.filter(tenant=tenant).delete()
                
                ShopPrice.objects.filter(tenant=tenant).delete()
                InventoryLedger.objects.filter(tenant=tenant).delete()
                Inventory.objects.filter(tenant=tenant).delete()
                Batch.objects.filter(tenant=tenant).delete()
                
                SaleItem.objects.filter(tenant=tenant).delete()
                Sale.objects.filter(tenant=tenant).delete()
                BankTransfer.objects.filter(tenant=tenant).delete()
                CashTransfer.objects.filter(tenant=tenant).delete()
                Shift.objects.filter(tenant=tenant).delete()
                CustomerTransaction.objects.filter(tenant=tenant).delete()
                Customer.objects.filter(tenant=tenant).delete()
                
                ECashLedger.objects.filter(tenant=tenant).delete()
                PaymentProviderConfig.objects.filter(tenant=tenant).delete()
                
                Product.objects.filter(tenant=tenant).delete()
                Category.objects.filter(tenant=tenant).delete()
                Location.objects.filter(tenant=tenant).delete()
                User.objects.filter(tenant=tenant).delete()
                tenant.delete()
            User.objects.filter(email__endswith='@demo.com').delete()

            # 2. Setup Tenants
            self.stdout.write("Creating Demo Tenants...")
            tenant_standard = Tenant.objects.create(
                name="Demo Company",
                currency="GHS",
                subscription_status="ACTIVE",
                phone="0241234567",
                allow_momo_payments=True
            )
            tenant_strict = Tenant.objects.create(
                name="Strict Demo Company",
                currency="GHS",
                subscription_status="ACTIVE",
                phone="0241234568",
                use_strict_sales_workflow=True,
                allow_momo_payments=True
            )

            # 3. Ensure Roles exist
            roles = {}
            role_names = ['ADMIN', 'AUDITOR', 'ACCOUNTANT', 'PRODUCTION_MANAGER', 'STORES_MANAGER', 'SHOP_MANAGER', 'SHOP_ATTENDANT', 'SHOP_CASHIER']
            for name in role_names:
                role, _ = Role.objects.get_or_create(name=name, defaults={'description': f'Demo {name}'})
                roles[name] = role

            for tenant, is_strict in [(tenant_standard, False), (tenant_strict, True)]:
                self._build_tenant_data(tenant, roles, is_strict)

        self.stdout.write(self.style.SUCCESS("Demo Environments successfully created!"))

    def _build_tenant_data(self, tenant, roles, is_strict):
        prefix = "strict_" if is_strict else ""
        
        # 4. Create Locations
        loc_production = Location.objects.create(tenant=tenant, name="Main Production", location_type="PRODUCTION", is_active=True)
        loc_stores = Location.objects.create(tenant=tenant, name="Central Warehouse", location_type="STORES", is_active=True)
        loc_shop1 = Location.objects.create(tenant=tenant, name="Downtown Shop", location_type="SHOP", is_active=True)
        loc_shop2 = Location.objects.create(tenant=tenant, name="Uptown Shop", location_type="SHOP", is_active=True)

        # 5. Create Users
        users_setup = [
            (f'{prefix}admin@demo.com', 'Admin', 'User', roles['ADMIN'], None),
            (f'{prefix}auditor@demo.com', 'System', 'Auditor', roles['AUDITOR'], None),
            (f'{prefix}accountant@demo.com', 'Chief', 'Accountant', roles['ACCOUNTANT'], None),
            (f'{prefix}production@demo.com', 'Prod', 'Manager', roles['PRODUCTION_MANAGER'], loc_production),
            (f'{prefix}stores@demo.com', 'Warehouse', 'Manager', roles['STORES_MANAGER'], loc_stores),
            (f'{prefix}manager1@demo.com', 'Downtown', 'Manager', roles['SHOP_MANAGER'], loc_shop1),
            (f'{prefix}attendant1@demo.com', 'Downtown', 'Attendant', roles['SHOP_ATTENDANT'], loc_shop1),
            (f'{prefix}manager2@demo.com', 'Uptown', 'Manager', roles['SHOP_MANAGER'], loc_shop2),
            (f'{prefix}attendant2@demo.com', 'Uptown', 'Attendant', roles['SHOP_ATTENDANT'], loc_shop2),
        ]
        
        if is_strict:
            users_setup.append((f'cashier_strict@demo.com', 'Downtown', 'Cashier', roles['SHOP_CASHIER'], loc_shop1))

        created_users = {}
        for email, fname, lname, role, loc in users_setup:
            u = User.objects.create_user(
                email=email,
                password='demo',
                first_name=fname,
                last_name=lname,
                tenant=tenant,
                role=role,
                location=loc,
                is_active=True
            )
            created_users[email] = u
                
        # Setup Payment Provider Configs
        paystack_config, _ = PaymentProviderConfig.objects.get_or_create(
            tenant=tenant, provider='PAYSTACK',
            defaults={'nickname': 'Main Paystack', 'is_active': True, 'public_key': 'demo_key'}
        )
        nalopay_config, _ = PaymentProviderConfig.objects.get_or_create(
            tenant=tenant, provider='NALOPAY',
            defaults={'nickname': 'Nalo Mobile', 'is_active': True, 'public_key': 'demo_key'}
        )
        appsnmobile_config, _ = PaymentProviderConfig.objects.get_or_create(
            tenant=tenant, provider='APPSNMOBILE',
            defaults={'nickname': 'AppsNMobile POS', 'is_active': True, 'public_key': 'demo_key'}
        )
        demo_providers = [paystack_config, nalopay_config, appsnmobile_config]

        # 6. Create Products with varying reorder_levels
        cat_stationery = Category.objects.create(tenant=tenant, name="Stationery")
        cat_toys = Category.objects.create(tenant=tenant, name="Toys")
        cat_snacks = Category.objects.create(tenant=tenant, name="Snacks")

        # (name, category, price, shop1_stock, shop2_stock, reorder_level, image)
        demo_items = [
            # Well stocked (GREEN badge)
            ("Blue Ink Pen", cat_stationery, '2.50', 200, 150, 20, 'blue_pen.png'),
            ("Fresh Cola 500ml", cat_snacks, '5.00', 120, 95, 15, 'fresh_cola.png'),
            # Approaching low stock (YELLOW badge: qty < threshold * 2)
            ("Exercise Book (80 pages)", cat_stationery, '5.00', 18, 25, 10, 'exercise_book.png'),
            ("Action Figure Toy", cat_toys, '45.00', 14, 20, 8, 'action_toy.png'),
            # Low / critical stock (RED badge: qty <= threshold)
            ("Chocolate Biscuit", cat_snacks, '6.00', 3, 5, 10, 'chocolate_biscuit.png'),
        ]

        products = []
        for name, cat, price, stock1, stock2, reorder, user_image in demo_items:
            p = Product.objects.create(
                tenant=tenant, category=cat, name=name, sku=name.replace(" ", "").upper()[:8],
                default_selling_price=Decimal(price), is_active=True,
                reorder_level=Decimal(str(reorder))
            )

            # Attach generated image if available
            img_path = os.path.join(settings.STATICFILES_DIRS[0], 'images', 'demo', user_image)
            if os.path.exists(img_path):
                with open(img_path, 'rb') as f:
                    p.image.save(user_image, File(f), save=True)

            products.append((p, stock1, stock2))

        # 7. Stock balances, Ledger entries and Shop Prices
        for p, stock1, stock2 in products:
            for shop, stock_qty in [(loc_shop1, stock1), (loc_shop2, stock2)]:
                InventoryLedger.objects.create(
                    tenant=tenant,
                    product=p,
                    location=shop,
                    transaction_type='IN',
                    quantity=Decimal(str(stock_qty)),
                    reference_type=f'DEMO-SEED-{p.pk}',
                    notes='Initial demo stock'
                )
                Inventory.objects.create(
                    tenant=tenant, location=shop, product=p, quantity=Decimal(str(stock_qty))
                )
                ShopPrice.objects.create(
                    tenant=tenant, location=shop, product=p,
                    selling_price=p.default_selling_price
                )

            for loc, qty in [(loc_stores, stock1 + stock2), (loc_production, stock1 * 2)]:
                InventoryLedger.objects.create(
                    tenant=tenant, product=p, location=loc,
                    transaction_type='IN', quantity=Decimal(str(qty)),
                    reference_type=f'DEMO-SEED-{p.pk}', notes='Initial demo stock'
                )
                Inventory.objects.create(
                    tenant=tenant, location=loc, product=p, quantity=Decimal(str(qty))
                )

        # 8. Create Customers with credit balances
        demo_customers = [
            ("Kwame Asante", "0241111111", loc_shop1, Decimal('120.00'), Decimal('500.00')),
            ("Ama Serwah", "0242222222", loc_shop1, Decimal('45.50'), Decimal('200.00')),
            ("Kofi Mensah", "0243333333", loc_shop1, Decimal('0.00'), Decimal('300.00')),
            ("Akua Boateng", "0244444444", loc_shop2, Decimal('230.00'), Decimal('500.00')),
            ("Yaw Darko", "0245555555", loc_shop2, Decimal('15.00'), Decimal('100.00')),
            ("Efua Nyarko", "0246666666", loc_shop2, Decimal('0.00'), Decimal('150.00')),
        ]

        attendant1 = created_users[f'{prefix}attendant1@demo.com']
        attendant2 = created_users[f'{prefix}attendant2@demo.com']

        for cname, cphone, cshop, balance, limit in demo_customers:
            customer = Customer.objects.create(
                tenant=tenant,
                name=cname,
                phone=cphone,
                shop=cshop,
                current_balance=balance,
                credit_limit=limit,
                is_active=True
            )
            if balance > 0:
                CustomerTransaction.objects.create(
                    tenant=tenant,
                    customer=customer,
                    transaction_type='DEBIT',
                    amount=balance,
                    description='Credit purchase (demo data)',
                    balance_before=Decimal('0.00'),
                    balance_after=balance,
                    performed_by=attendant1 if cshop == loc_shop1 else attendant2
                )

        # 9. Create Mock Sales and Cash transfers
        now = timezone.now()

        # Create a shift
        shift = Shift.objects.create(
            tenant=tenant,
            shop=loc_shop1,
            attendant=created_users[f'{prefix}attendant1@demo.com'],
            start_time=now - timedelta(hours=8),
            status='OPEN',
            opening_cash=Decimal('50.00'),
        )

        for i in range(25):
            days_ago = random.randint(0, 15)
            sale_time = now - timedelta(days=days_ago, hours=random.randint(1, 10))

            shop = random.choice([loc_shop1, loc_shop2])
            attendant = created_users[f'{prefix}attendant1@demo.com'] if shop == loc_shop1 else created_users[f'{prefix}attendant2@demo.com']
            payment_method = random.choice(['CASH', 'CASH', 'ECASH', 'CREDIT'])
            
            # In strict workflow, recent sales might be PENDING at cashier desk
            status = 'COMPLETED'
            cashier = None
            if is_strict and days_ago == 0 and shop == loc_shop1:
                status = random.choice(['PENDING', 'COMPLETED'])
                if status == 'COMPLETED':
                    cashier = created_users.get('cashier_strict@demo.com')

            sale_customer = None
            if payment_method == 'CREDIT':
                shop_customers = Customer.objects.filter(tenant=tenant, shop=shop, is_active=True)
                if shop_customers.exists():
                    sale_customer = random.choice(list(shop_customers))

            sale = Sale.objects.create(
                tenant=tenant, shop=shop, attendant=attendant, cashier=cashier,
                shift=shift if shop == loc_shop1 else None,
                status=status, payment_method=payment_method, amount_paid=Decimal('0'),
                sale_number=f"DEMO-SL-{random.randint(10000, 99999)}",
                customer=sale_customer
            )
            Sale.objects.filter(pk=sale.pk).update(created_at=sale_time)

            sale_total = Decimal('0')
            for _ in range(random.randint(1, 4)):
                p, _stock1, _stock2 = random.choice(products)
                qty = Decimal(str(random.randint(1, 5)))
                item_total = p.default_selling_price * qty
                SaleItem.objects.create(
                    tenant=tenant, sale=sale, product=p, quantity=qty,
                    unit_price=p.default_selling_price, total=item_total
                )
                sale_total += item_total

            sale.total = sale_total
            sale.subtotal = sale_total
            if status == 'COMPLETED':
                sale.amount_paid = sale_total if payment_method in ['CASH', 'ECASH'] else Decimal('0')
            sale.save()
            
            # Seed E-Cash Ledger if ECASH payment
            if payment_method == 'ECASH' and status == 'COMPLETED':
                provider_config = random.choice(demo_providers)
                ECashLedger.objects.create(
                    tenant=tenant,
                    shop=shop,
                    transaction_type='PAYMENT',
                    amount=sale_total,
                    reference_type='Sale',
                    reference_id=sale.pk,
                    provider_config=provider_config,
                    provider=provider_config.provider,
                    created_by=attendant,
                    notes=f"Demo E-Cash Sale"
                )
                # Ensure date is consistent
                ECashLedger.objects.filter(reference_type='Sale', reference_id=sale.pk).update(created_at=sale_time)

        # 10. Create mock cash transfers
        for _ in range(5):
            days_ago = random.randint(1, 15)
            transfer_time = now - timedelta(days=days_ago)
            shop = random.choice([loc_shop1, loc_shop2])
            manager = created_users[f'{prefix}manager1@demo.com'] if shop == loc_shop1 else created_users[f'{prefix}manager2@demo.com']
            ct = CashTransfer.objects.create(
                tenant=tenant, from_location=shop, from_user=manager, to_user=created_users[f'{prefix}accountant@demo.com'],
                transfer_type='DEPOSIT', amount=Decimal(str(random.randint(50, 200))), status='CONFIRMED',
                confirmed_at=transfer_time
            )
            CashTransfer.objects.filter(pk=ct.pk).update(created_at=transfer_time)

            # Randomly create some Bank Transfers
            if random.choice([True, False]):
                fund_source = random.choice(['CASH', 'ECASH', 'MOMO'])
                provider_config = random.choice(demo_providers) if fund_source == 'ECASH' else None
                
                bt = BankTransfer.objects.create(
                    tenant=tenant, 
                    accountant=created_users[f'{prefix}accountant@demo.com'],
                    amount=Decimal(str(random.randint(100, 500))),
                    fund_source=fund_source,
                    provider_config=provider_config,
                    teller_name=f"Teller {random.randint(1,5)}",
                    notes="Daily deposit" if random.choice([True, False]) else ""
                )
                BankTransfer.objects.filter(pk=bt.pk).update(created_at=transfer_time)
                
                # If ECASH, deduct from ledger
                if fund_source == 'ECASH':
                    ECashLedger.objects.create(
                        tenant=tenant,
                        transaction_type='WITHDRAWAL',
                        amount=-bt.amount,
                        reference_type='BankTransfer',
                        reference_id=bt.pk,
                        provider_config=provider_config,
                        provider=provider_config.provider,
                        created_by=bt.accountant,
                        notes=f"Demo Bank Transfer via {provider_config.nickname}"
                    )
                    ECashLedger.objects.filter(reference_type='BankTransfer', reference_id=bt.pk).update(created_at=transfer_time)

        # 11. Explicitly seed PENDING sales for Cashier Queue in strict workflow
        if is_strict:
            for i in range(4):
                sale = Sale.objects.create(
                    tenant=tenant, shop=loc_shop1, attendant=created_users[f'{prefix}attendant1@demo.com'],
                    shift=shift, status='PENDING', payment_method=random.choice(['CASH', 'MOMO']),
                    amount_paid=Decimal('0'), sale_number=f"DEMO-SL-P{random.randint(100, 999)}"
                )
                
                sale_total = Decimal('0')
                for _ in range(2):
                    p, _stock1, _stock2 = random.choice(products)
                    qty = Decimal(str(random.randint(1, 3)))
                    item_total = p.default_selling_price * qty
                    SaleItem.objects.create(
                        tenant=tenant, sale=sale, product=p, quantity=qty,
                        unit_price=p.default_selling_price, total=item_total
                    )
                    sale_total += item_total

                sale.total = sale_total
                sale.subtotal = sale_total
                sale.save()
