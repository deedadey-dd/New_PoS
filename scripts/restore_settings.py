import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

from apps.payments.models import PaymentProviderConfig, ShopPaymentAssignment
from apps.core.models import Location

def restore():
    try:
        with open('payment_settings_backup.json', 'r') as f:
            data = json.load(f)
            
        for row in data:
            config = PaymentProviderConfig.objects.create(
                tenant_id=row['tenant_id'],
                provider=row['provider'],
                nickname=f"Default {row['provider'].capitalize()}",
                is_active=row['is_active'],
                public_key=row['public_key'],
                _secret_key=row['secret_key'],
                _webhook_secret=row['webhook_secret'],
                callback_url=row['callback_url'],
                test_mode=row['test_mode']
            )
            print(f"Restored config for tenant {row['tenant_id']}")
            
            # Create default assignment for all shops in this tenant
            shops = Location.objects.filter(tenant_id=row['tenant_id'], location_type='SHOP')
            for shop in shops:
                ShopPaymentAssignment.objects.create(
                    tenant_id=row['tenant_id'],
                    shop=shop,
                    provider_config=config,
                    is_default=True,
                    priority=0
                )
                print(f"Created assignment for shop {shop.name}")
                
        print("Restore complete.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    restore()
