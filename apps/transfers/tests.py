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


from apps.transfers.models import StockRequest, StockRequestItem

class StockRequestCombineTests(TestCase):
    def setUp(self):
        # Setup similar basic data
        self.tenant = Tenant.objects.create(name="Combine Test Tenant")
        self.stores_role, _ = Role.objects.get_or_create(name='STORES_MANAGER')
        self.shop_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        
        self.stores = Location.objects.create(tenant=self.tenant, name="Main Store", location_type='STORES')
        self.shop = Location.objects.create(tenant=self.tenant, name="Branch 1", location_type='SHOP')
        
        self.store_manager = User.objects.create(email="stores2@test.com", tenant=self.tenant, role=self.stores_role, location=self.stores)
        self.shop_manager = User.objects.create(email="shop2@test.com", tenant=self.tenant, role=self.shop_role, location=self.shop)
        
        self.category = Category.objects.create(tenant=self.tenant, name="Electronics")
        self.product1 = Product.objects.create(tenant=self.tenant, name="Laptop", sku="LPT", category=self.category, default_selling_price=Decimal('1000.00'))
        self.product2 = Product.objects.create(tenant=self.tenant, name="Mouse", sku="MOU", category=self.category, default_selling_price=Decimal('50.00'))

    def test_combine_stock_requests(self):
        # Create requests
        req1 = StockRequest.objects.create(
            tenant=self.tenant, requesting_location=self.shop, supplying_location=self.stores, 
            status='PENDING', requested_by=self.shop_manager
        )
        StockRequestItem.objects.create(tenant=self.tenant, request=req1, product=self.product1, quantity_requested=Decimal('5'), notes='Urgent')
        
        req2 = StockRequest.objects.create(
            tenant=self.tenant, requesting_location=self.shop, supplying_location=self.stores, 
            status='PENDING', requested_by=self.shop_manager
        )
        StockRequestItem.objects.create(tenant=self.tenant, request=req2, product=self.product1, quantity_requested=Decimal('3'), notes='For new staff')
        StockRequestItem.objects.create(tenant=self.tenant, request=req2, product=self.product2, quantity_requested=Decimal('10'))

        self.client.force_login(self.store_manager)
        
        # Combine requests via API
        response = self.client.post(
            '/transfers/requests/combine/', 
            {'request_ids': [req1.id, req2.id]},
            content_type='application/json'
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('success'))
        
        # Check resulting transfer
        req1.refresh_from_db()
        req2.refresh_from_db()
        
        self.assertEqual(req1.status, 'CONVERTED')
        self.assertEqual(req2.status, 'CONVERTED')
        self.assertIsNotNone(req1.resulting_transfer)
        self.assertEqual(req1.resulting_transfer, req2.resulting_transfer)
        
        transfer = req1.resulting_transfer
        self.assertEqual(transfer.status, 'DRAFT')
        self.assertEqual(transfer.source_location, self.stores)
        self.assertEqual(transfer.destination_location, self.shop)
        
        # Check items aggregated
        items = transfer.items.all().order_by('product__name')
        self.assertEqual(items.count(), 2)
        
        # Laptop should have 5 + 3 = 8
        laptop_item = items.get(product=self.product1)
        self.assertEqual(laptop_item.quantity_requested, Decimal('8'))
        self.assertIn('Urgent', laptop_item.notes)
        self.assertIn('For new staff', laptop_item.notes)
        
        # Mouse should have 10
        mouse_item = items.get(product=self.product2)
        self.assertEqual(mouse_item.quantity_requested, Decimal('10'))

class InterShopTransferTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="InterShop Tenant", allow_inter_shop_transfers=True)
        self.shop_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        
        self.shop1 = Location.objects.create(tenant=self.tenant, name="Shop A", location_type='SHOP')
        self.shop2 = Location.objects.create(tenant=self.tenant, name="Shop B", location_type='SHOP')
        
        self.manager1 = User.objects.create(email="m1@test.com", tenant=self.tenant, role=self.shop_role, location=self.shop1)
        self.manager2 = User.objects.create(email="m2@test.com", tenant=self.tenant, role=self.shop_role, location=self.shop2)
        
    def test_inter_shop_transfer_allowed(self):
        from apps.transfers.forms import TransferForm
        form = TransferForm(tenant=self.tenant, user=self.manager1, data={
            'source_location': self.shop1.id,
            'destination_location': self.shop2.id,
            'notes': 'Test'
        })
        # It should be valid
        self.assertTrue(form.is_valid(), form.errors)
            
    def test_inter_shop_transfer_disallowed(self):
        self.tenant.allow_inter_shop_transfers = False
        self.tenant.save()
        
        from apps.transfers.forms import TransferForm
        form = TransferForm(tenant=self.tenant, user=self.manager1, data={
            'source_location': self.shop1.id,
            'destination_location': self.shop2.id,
            'notes': 'Test'
        })
        self.assertFalse(form.is_valid())
        self.assertIn('destination_location', form.errors)
            
    def test_inter_shop_request_allowed(self):
        from apps.transfers.forms import StockRequestForm
        form = StockRequestForm(tenant=self.tenant, user=self.manager1, data={
            'requesting_location': self.shop1.id,
            'supplying_location': self.shop2.id,
            'notes': 'Test'
        })
        self.assertTrue(form.is_valid(), form.errors)
            
    def test_inter_shop_request_disallowed(self):
        self.tenant.allow_inter_shop_transfers = False
        self.tenant.save()
        
        from apps.transfers.forms import StockRequestForm
        form = StockRequestForm(tenant=self.tenant, user=self.manager1, data={
            'requesting_location': self.shop1.id,
            'supplying_location': self.shop2.id,
            'notes': 'Test'
        })
        self.assertFalse(form.is_valid())
        self.assertIn('supplying_location', form.errors)
