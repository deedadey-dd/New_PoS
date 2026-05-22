from django.test import TestCase
from decimal import Decimal
from apps.core.models import Tenant, User, Location, Role
from apps.inventory.models import Category, Product, Batch, InventoryLedger, StockAdjustment
from apps.transfers.models import Transfer, TransferItem

class TransferDiscrepancyTests(TestCase):
    def setUp(self):
        # Setup basic data
        self.tenant = Tenant.objects.create(name="Discrepancy Test Tenant")
        
        # Roles
        self.admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        self.stores_role, _ = Role.objects.get_or_create(name='STORES_MANAGER')
        self.shop_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        
        # Locations
        self.stores = Location.objects.create(tenant=self.tenant, name="Main Store", location_type='STORES')
        self.shop = Location.objects.create(tenant=self.tenant, name="Branch 1", location_type='SHOP')
        
        # Users
        self.admin = User.objects.create(email="admin@test.com", tenant=self.tenant, role=self.admin_role)
        self.store_manager = User.objects.create(email="stores@test.com", tenant=self.tenant, role=self.stores_role, location=self.stores)
        self.shop_manager = User.objects.create(email="shop@test.com", tenant=self.tenant, role=self.shop_role, location=self.shop)
        
        # Product
        self.category = Category.objects.create(tenant=self.tenant, name="Electronics")
        self.product = Product.objects.create(
            tenant=self.tenant, 
            name="Laptop", 
            sku="LPT-001",
            category=self.category,
            default_selling_price=Decimal('1000.00')
        )
        
        # Initial Stock in Stores
        self.batch = Batch.objects.create(
            tenant=self.tenant,
            product=self.product,
            location=self.stores,
            batch_number="BATCH-001",
            unit_cost=Decimal('500.00'),
            initial_quantity=Decimal('100'),
            current_quantity=Decimal('100')
        )
        InventoryLedger.objects.create(
            tenant=self.tenant,
            product=self.product,
            batch=self.batch,
            location=self.stores,
            transaction_type='IN',
            quantity=Decimal('100'),
            unit_cost=Decimal('500.00'),
            notes="Initial Stock",
            created_by=self.admin
        )

    def test_receive_short_quantity_restores_sender_stock(self):
        """
        Verify that receiving a transfer with discrepancy reason 'SHORT' 
        automatically creates a DISPUTE_REVERSAL to return the missing stock to the sender.
        """
        # Create transfer
        transfer = Transfer.objects.create(
            tenant=self.tenant,
            source_location=self.stores,
            destination_location=self.shop,
            created_by=self.store_manager
        )
        
        item = TransferItem.objects.create(
            tenant=self.tenant,
            transfer=transfer,
            product=self.product,
            batch=self.batch,
            quantity_requested=Decimal('10'),
            unit_cost=self.batch.unit_cost
        )
        
        # Send
        transfer.send(self.store_manager)
        
        # Receiver gets only 8 (2 short)
        discrepancy_data = {
            str(item.pk): {
                'reason': 'SHORT',
                'notes': 'Missing from box'
            }
        }
        items_received = {str(item.pk): Decimal('8')}
        
        # Receive
        transfer.receive(self.shop_manager, items_received, discrepancy_data)
        
        item.refresh_from_db()
        self.assertEqual(item.quantity_received, Decimal('8'))
        
        # Verify sender got the 2 short items back via DISPUTE_REVERSAL
        reversal = InventoryLedger.objects.filter(
            tenant=self.tenant,
            location=self.stores,
            transaction_type='DISPUTE_REVERSAL',
            reference_id=transfer.pk
        ).first()
        
        self.assertIsNotNone(reversal, "A DISPUTE_REVERSAL should be created at the sender location for SHORT reasons.")
        self.assertEqual(reversal.quantity, Decimal('2'))
        
        # Verify NO StockAdjustment was created
        adjustments = StockAdjustment.objects.filter(tenant=self.tenant)
        self.assertEqual(adjustments.count(), 0, "No StockAdjustment should be created for SHORT reasons.")

    def test_receive_damaged_creates_stock_adjustment_at_receiver(self):
        """
        Verify that receiving a transfer with discrepancy reason 'DAMAGED' 
        adds the full sent quantity to the receiver, then creates a pending StockAdjustment
        to write-off the damaged quantity.
        """
        # Create transfer
        transfer = Transfer.objects.create(
            tenant=self.tenant,
            source_location=self.stores,
            destination_location=self.shop,
            created_by=self.store_manager
        )
        
        item = TransferItem.objects.create(
            tenant=self.tenant,
            transfer=transfer,
            product=self.product,
            batch=self.batch,
            quantity_requested=Decimal('10'),
            unit_cost=self.batch.unit_cost
        )
        
        # Send
        transfer.send(self.store_manager)
        
        # Receiver gets 7 intact (3 damaged)
        discrepancy_data = {
            str(item.pk): {
                'reason': 'DAMAGED',
                'notes': 'Crushed in transit'
            }
        }
        items_received = {str(item.pk): Decimal('7')}
        
        # Receive
        transfer.receive(self.shop_manager, items_received, discrepancy_data)
        
        item.refresh_from_db()
        self.assertEqual(item.quantity_received, Decimal('7'))
        
        # Verify sender did NOT get the items back
        reversal = InventoryLedger.objects.filter(
            tenant=self.tenant,
            location=self.stores,
            transaction_type='DISPUTE_REVERSAL',
            reference_id=transfer.pk
        ).first()
        self.assertIsNone(reversal, "No DISPUTE_REVERSAL should be created for DAMAGED reasons.")
        
        # Verify receiver got a TRANSFER_IN for the discrepancy (3 items)
        transfer_in = InventoryLedger.objects.filter(
            tenant=self.tenant,
            location=self.shop,
            transaction_type='TRANSFER_IN',
            quantity=Decimal('3'),  # The discrepancy quantity
            reference_id=transfer.pk
        ).first()
        self.assertIsNotNone(transfer_in, "A TRANSFER_IN should be created for the discrepancy quantity at the receiver.")
        
        # Verify a StockAdjustment was created at the receiver location
        adjustment = StockAdjustment.objects.filter(
            tenant=self.tenant,
            location=self.shop,
            product=self.product,
            adjustment_type='DAMAGE',
            status='PENDING'
        ).first()
        
        self.assertIsNotNone(adjustment, "A pending StockAdjustment should be created for DAMAGED reasons.")
        self.assertEqual(adjustment.quantity, Decimal('-3'))  # Negative to remove
        self.assertIn('Damaged in Transit', adjustment.reason)
