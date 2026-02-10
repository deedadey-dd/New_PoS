from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.utils import timezone
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.sales.models import Sale
from apps.inventory.models import Product, Inventory
from apps.sync.models import SyncLog
from .serializers import TransactionSerializer, ProductSerializer, InventorySerializer

class SyncTransactionView(APIView):
    """
    Receive single transaction from device/local server.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        client_id = request.data.get('client_id')
        if not client_id:
            return Response({"error": "client_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Idempotency check
        if Sale.objects.filter(client_id=client_id, tenant=request.user.tenant).exists():
            sale = Sale.objects.get(client_id=client_id, tenant=request.user.tenant)
            return Response({
                "status": "exists",
                "server_id": sale.pk,
                "message": "Transaction already exists"
            }, status=status.HTTP_200_OK)

        # Prepare data
        data = request.data.copy()
        target_status = data.get('status', 'PENDING')
        
        # If sending as COMPLETED, we must save as PENDING first to allow complete() workflow
        if target_status == 'COMPLETED':
            data['status'] = 'PENDING'

        serializer = TransactionSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            with transaction.atomic():
                sale = serializer.save(tenant=request.user.tenant)
                
                # If it was supposed to be COMPLETED, complete it now to trigger side effects (inventory, etc.)
                if target_status == 'COMPLETED':
                    try:
                        amount_paid = request.data.get('amount_paid', 0)
                        payment_method = request.data.get('payment_method', 'CASH')
                        paystack_ref = request.data.get('paystack_reference', '')
                        sale.complete(amount_paid, payment_method, paystack_ref)
                    except Exception as e:
                        # Rollback is handled by atomic block if we raise here
                        # Log specific error if needed
                        raise e

                # Log sync
                SyncLog.objects.create(
                    tenant=request.user.tenant,
                    device_id=request.data.get('device_id', 'unknown'),
                    device_type=request.data.get('device_type', 'unknown'),
                    sync_direction='device_to_server',
                    entity_type='Sale',
                    entity_id=sale.client_id,
                    status='success',
                    sale_transaction=sale
                )
            return Response({
                "status": "created",
                "server_id": sale.pk
            }, status=status.HTTP_201_CREATED)
        
        # Log failure
        import sys
        print(f"Sync Validation Error: {serializer.errors}", file=sys.stderr)
        SyncLog.objects.create(
            tenant=request.user.tenant,
            device_id=request.data.get('device_id', 'unknown'),
            device_type=request.data.get('device_type', 'unknown'),
            sync_direction='device_to_server',
            entity_type='Sale',
            entity_id=client_id,
            status='failed',
            error_message=str(serializer.errors)
        )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SyncBatchView(APIView):
    """
    Receive batch of changes (transactions, inventory updates).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        transactions = request.data.get('transactions', [])
        inventory_updates = request.data.get('inventory', [])
        
        results = {
            'transactions': [],
            'inventory': []
        }

        # Process Transactions
        for tx_data in transactions:
            client_id = tx_data.get('client_id')
            # Reuse logic or call serializer (simplified here)
            # ...
            results['transactions'].append({'client_id': client_id, 'status': 'processed'})

        return Response(results)


class GetUpdatesView(APIView):
    """
    Return all changes since `last_sync` timestamp.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        last_sync_str = request.query_params.get('last_sync')
        last_sync = parse_datetime(last_sync_str) if last_sync_str else None
        
        current_server_time = timezone.now()
        
        response_data = {
            'server_timestamp': current_server_time,
            'products': [],
            'inventory': [],
            'settings': [] # To implement
        }

        if last_sync:
            # Get updated products
            products = Product.objects.filter(
                tenant=request.user.tenant, 
                updated_at__gt=last_sync
            )
            response_data['products'] = ProductSerializer(products, many=True).data
            
            # Get updated inventory snapshots
            inventory = Inventory.objects.filter(
                tenant=request.user.tenant,
                updated_at__gt=last_sync
            )
            response_data['inventory'] = InventorySerializer(inventory, many=True).data
        else:
            # Full sync (initial load) - maybe paginate this or limit
            products = Product.objects.filter(tenant=request.user.tenant, is_active=True)
            response_data['products'] = ProductSerializer(products, many=True).data
            
            inventory = Inventory.objects.filter(tenant=request.user.tenant)
            response_data['inventory'] = InventorySerializer(inventory, many=True).data

        return Response(response_data)


@api_view(['GET'])
@permission_classes([AllowAny]) # Or generic check
def health_check(request):
    from django.db import connection
    try:
        connection.ensure_connection()
        db_status = True
    except Exception:
        db_status = False
        
    return Response({
        "status": "ok",
        "timestamp": timezone.now(),
        "database": "connected" if db_status else "error"
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_sync_status(request):
    """
    Check status of sync for a device.
    """
    device_id = request.query_params.get('device_id')
    # logic to check pending items for this device?
    # For now return server time
    return Response({
        "server_time": timezone.now(),
        "pending_downstream": 0 # Placeholder
    })
