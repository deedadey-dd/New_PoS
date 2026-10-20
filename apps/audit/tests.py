from decimal import Decimal
from django.test import TestCase, RequestFactory
from django.utils import timezone
from apps.core.models import Tenant, Role, User, Location
from apps.inventory.models import Product, Batch, InventoryLedger
from apps.audit.views import ProductLifecycleView


class ProductLifecycleViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(name="Audit Test Corp")
        self.role_admin = Role.objects.create(name="ADMIN")
        
        self.user = User.objects.create_user(
            email="auditor@test.com",
            password="password123",
            tenant=self.tenant,
            role=self.role_admin
        )
        
        self.location_a = Location.objects.create(
            name="Main Store",
            location_type="STORES",
            tenant=self.tenant
        )
        self.location_b = Location.objects.create(
            name="Branch Shop",
            location_type="SHOP",
            tenant=self.tenant
        )
        
        self.product = Product.objects.create(
            tenant=self.tenant,
            name="Super Biscuit",
            sku="SB-001",
            default_selling_price=Decimal("10.00")
        )

    def test_summary_computations_and_reconciliation(self):
        """Test that all inflows, outflows, and locations are correctly categorized and balance."""
        # 1. Bulk Receive (+20)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='IN',
            quantity=Decimal('20.00'),
            reference_type='BulkBatch',
            notes='Bulk receive — Group GRP-1'
        )
        
        # 2. Batch Receive (+30)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='IN',
            quantity=Decimal('30.00'),
            reference_type='Batch',
            notes='Initial stock receipt - Batch B-01'
        )
        
        # 3. Seed / Initial (+50)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='IN',
            quantity=Decimal('50.00'),
            reference_type='DEMO-SEED',
            notes='Initial demo stock'
        )
        
        # 4. Stock Adjustment Add (+10)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='ADJUST',
            quantity=Decimal('10.00'),
            reference_type='StockAdjustment'
        )
        
        # 5. Sale Return / Refund (+2)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_b,
            transaction_type='SALE_RETURN',
            quantity=Decimal('2.00'),
            reference_type='Refund'
        )
        
        # 6. Dispute Reversal Restored (+3)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='DISPUTE_REVERSAL',
            quantity=Decimal('3.00'),
            reference_type='Transfer'
        )
        
        # Total Entries should be: 20 + 30 + 50 + 10 + 2 + 3 = 115
        
        # 7. Sales (-25)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_b,
            transaction_type='SALE',
            quantity=Decimal('-25.00'),
            reference_type='Sale'
        )
        
        # 8. Transfer Out (-15)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='TRANSFER_OUT',
            quantity=Decimal('-15.00'),
            reference_type='Transfer'
        )
        
        # 9. Transfer In (+15)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_b,
            transaction_type='TRANSFER_IN',
            quantity=Decimal('15.00'),
            reference_type='Transfer'
        )
        
        # Updated Total Entries: 115 + 15 = 130
        
        # 10. Damage (-5)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_b,
            transaction_type='DAMAGE',
            quantity=Decimal('-5.00'),
            reference_type='WriteOff'
        )
        
        # 11. Stock Adjustment Deduction (-4)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='ADJUST',
            quantity=Decimal('-4.00'),
            reference_type='StockAdjustment'
        )
        
        # Total Exits: -25 (Sale) - 15 (Tr_Out) - 5 (Damage) - 4 (Adj) = -49
        # Net Movement: 130 - 49 = 81
        
        self.client.force_login(self.user)
        response = self.client.get(f'/audit/product-lifecycle/{self.product.pk}/')
        
        self.assertEqual(response.status_code, 200)
        
        # Check context calculations
        c = response.context
        self.assertEqual(c['bulk_receive_qty'], Decimal('20.00'))
        self.assertEqual(c['batch_receive_qty'], Decimal('30.00'))
        self.assertEqual(c['initial_stock_qty'], Decimal('50.00'))
        self.assertEqual(c['adjust_pos_qty'], Decimal('10.00'))
        self.assertEqual(c['returns_qty'], Decimal('2.00'))
        self.assertEqual(c['dispute_qty'], Decimal('3.00'))
        self.assertEqual(c['transfer_in_qty'], Decimal('15.00'))
        self.assertEqual(c['total_entries_qty'], Decimal('130.00'))
        
        self.assertEqual(c['sales_qty'], Decimal('-25.00'))
        self.assertEqual(c['transfer_out_qty'], Decimal('-15.00'))
        self.assertEqual(c['damage_qty'], Decimal('-5.00'))
        self.assertEqual(c['adjust_neg_qty'], Decimal('-4.00'))
        self.assertEqual(c['total_exits_qty'], Decimal('-49.00'))
        
        self.assertEqual(c['current_total_stock'], Decimal('81.00'))
        self.assertEqual(c['period_net_movement'], Decimal('81.00'))
        
        # Mathematical reconciliation
        self.assertEqual(c['total_entries_qty'] + c['total_exits_qty'], c['current_total_stock'])
        
        # Location balances check
        loc_balances = {loc['location__name']: loc['current_stock'] for loc in c['location_balances']}
        # Location A: 20 + 30 + 50 + 10 + 3 - 15 - 4 = 94
        self.assertEqual(loc_balances['Main Store'], Decimal('94.00'))
        # Location B: 2 (Return) + 15 (Tr_In) - 25 (Sale) - 5 (Damage) = -13
        self.assertEqual(loc_balances['Branch Shop'], Decimal('-13.00'))
        self.assertEqual(sum(loc_balances.values()), Decimal('81.00'))

    def test_date_filter_preserves_current_stock_and_shows_period_movement(self):
        """Test that date filtering only restricts entries/exits to the period, while preserving true current stock."""
        today = timezone.localdate()
        yesterday = today - timezone.timedelta(days=1)
        two_days_ago = today - timezone.timedelta(days=2)
        
        # Inflow 2 days ago (+100)
        e1 = InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='IN',
            quantity=Decimal('100.00'),
            reference_type='Batch'
        )
        InventoryLedger.objects.filter(pk=e1.pk).update(created_at=timezone.now() - timezone.timedelta(days=2))
        
        # Sale yesterday (-10)
        e2 = InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_a,
            transaction_type='SALE',
            quantity=Decimal('-10.00'),
            reference_type='Sale'
        )
        InventoryLedger.objects.filter(pk=e2.pk).update(created_at=timezone.now() - timezone.timedelta(days=1))
        
        # Filter for yesterday only
        self.client.force_login(self.user)
        response = self.client.get(
            f'/audit/product-lifecycle/{self.product.pk}/?date_from={yesterday}&date_to={yesterday}'
        )
        self.assertEqual(response.status_code, 200)
        c = response.context
        
        # Period entries should be 0, exits -10
        self.assertEqual(c['total_entries_qty'], Decimal('0.00'))
        self.assertEqual(c['total_exits_qty'], Decimal('-10.00'))
        self.assertEqual(c['period_net_movement'], Decimal('-10.00'))
        
        # Overall current total stock must still reflect all-time stock: 100 - 10 = 90
        self.assertEqual(c['current_total_stock'], Decimal('90.00'))
        
        # Location balance must show current stock 90, and period movement -10
        loc_entry = [loc for loc in c['location_balances'] if loc['location__id'] == self.location_a.pk][0]
        self.assertEqual(loc_entry['current_stock'], Decimal('90.00'))
        self.assertEqual(loc_entry['period_movement'], Decimal('-10.00'))

    def test_date_swap_when_from_after_to(self):
        """Test that invalid date ranges (from > to) are automatically swapped."""
        today = timezone.localdate()
        yesterday = today - timezone.timedelta(days=1)
        
        self.client.force_login(self.user)
        response = self.client.get(
            f'/audit/product-lifecycle/{self.product.pk}/?date_from={today}&date_to={yesterday}'
        )
        self.assertEqual(response.status_code, 200)
        c = response.context
        self.assertEqual(c['date_from'], str(yesterday))
        self.assertEqual(c['date_to'], str(today))

    def test_strict_sales_workflow_partial_and_pending_dispatch(self):
        """Test strict sales workflow: partial dispatch in ledger and pending dispatch tracking."""
        from apps.sales.models import Sale, SaleItem
        self.tenant.use_strict_sales_workflow = True
        self.tenant.save()

        # 1. Initial stock in location B (+20)
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_b,
            transaction_type='IN',
            quantity=Decimal('20.00'),
            reference_type='Batch',
            notes='Initial receipt'
        )

        # 2. Strict workflow sale: 10 units ordered, paid, status PENDING_DISPATCH
        sale = Sale.objects.create(
            tenant=self.tenant,
            shop=self.location_b,
            attendant=self.user,
            status='PENDING_DISPATCH',
            payment_method='CASH',
            total=Decimal('100.00'),
            amount_paid=Decimal('100.00'),
            is_dispatched=False
        )
        item = SaleItem.objects.create(
            tenant=self.tenant,
            sale=sale,
            product=self.product,
            quantity=Decimal('10.00'),
            dispatched_quantity=Decimal('4.00'),  # 4 dispatched, 6 pending
            unit_price=Decimal('10.00'),
            total=Decimal('100.00')
        )

        # 3. Ledger entry for the 4 units partially dispatched
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.location_b,
            transaction_type='SALE',
            quantity=Decimal('-4.00'),
            reference_type='Sale',
            reference_id=sale.pk,
            notes=f"Partial dispatch 4.00/10.00 — Sale {sale.sale_number}"
        )

        self.client.force_login(self.user)
        response = self.client.get(f'/audit/product-lifecycle/{self.product.pk}/')
        self.assertEqual(response.status_code, 200)
        c = response.context

        # Physical stock on hand: 20 - 4 = 16
        self.assertEqual(c['current_total_stock'], Decimal('16.00'))
        # Sales dispatched in ledger: -4
        self.assertEqual(c['sales_qty'], Decimal('-4.00'))
        # Pending dispatch: 10 - 4 = 6
        self.assertEqual(c['total_pending_dispatch_qty'], Decimal('6.00'))
        # Available stock to sell: 16 - 6 = 10
        self.assertEqual(c['available_total_stock'], Decimal('10.00'))
        # Total committed to sales: 4 + 6 = 10
        self.assertEqual(c['total_sales_committed'], Decimal('10.00'))

        # Check location balances
        loc_b = [loc for loc in c['location_balances'] if loc['location__id'] == self.location_b.pk][0]
        self.assertEqual(loc_b['current_stock'], Decimal('16.00'))
        self.assertEqual(loc_b['pending_dispatch'], Decimal('6.00'))
        self.assertEqual(loc_b['available_stock'], Decimal('10.00'))

