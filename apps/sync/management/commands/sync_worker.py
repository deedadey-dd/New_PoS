import time
import requests
import json
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from apps.sync.models import SyncQueue, SyncLog
from apps.sales.models import Sale

class Command(BaseCommand):
    help = 'Runs the continuous sync worker for Local <-> Central server synchronization'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting Sync Worker..."))
        
        server_url = getattr(settings, 'SYNC_SERVER_URL', None)
        auth_token = getattr(settings, 'SYNC_TOKEN', None)
        
        if not server_url:
            self.stdout.write(self.style.WARNING("SYNC_SERVER_URL not configured. Running in standalone mode (no backend sync)."))
            # Just loop idly or exit? If electron uses this, maybe just exit or sleep.
            # But maybe we want the process to stay alive.
            try:
                while True: time.sleep(60)
            except KeyboardInterrupt:
                return

        headers = {
            'Authorization': f'Bearer {auth_token}',
            'Content-Type': 'application/json',
            'X-Device-ID': 'server_instance'
        }

        while True:
            try:
                self.sync_process(server_url, headers)
            except KeyboardInterrupt:
                self.stdout.write("Stopping Sync Worker...")
                break
            except Exception as e:
                self.stderr.write(f"Sync Worker Error: {e}")
            
            time.sleep(30)

    def sync_process(self, base_url, headers):
        # 1. Push Pending Transactions
        # In a real scenario, we might query Sale objects that are pending, or check SyncQueue
        pending_sales = Sale.objects.filter(sync_status='pending', status='COMPLETED')
        
        for sale in pending_sales:
            try:
                # Serialize sale (simplified)
                payload = {
                    'client_id': sale.client_id or f"srv_{sale.pk}",
                    'total': str(sale.total),
                    'items': [], # Populate items
                    # ...
                }
                
                # Use serializers if available, but management command might want raw dicts
                # For brevity, assuming endpoint accepts what we send
                
                resp = requests.post(f"{base_url}/api/transactions/", json=payload, headers=headers, timeout=10)
                
                if resp.status_code in [200, 201]:
                     sale.sync_status = 'synced'
                     sale.synced_at = timezone.now()
                     sale.save(update_fields=['sync_status', 'synced_at'])
                     self.stdout.write(f"Synced Sale {sale.sale_number}")
                else:
                    self.stderr.write(f"Failed to sync Sale {sale.sale_number}: {resp.text}")
                    sale.retry_count += 1
                    sale.last_error = resp.text[:200]
                    sale.save()
                    
            except Exception as ex:
                self.stderr.write(f"Error syncing sale {sale.pk}: {ex}")

        # 2. Pull Updates
        # ...
