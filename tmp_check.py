import os
import sys
import django
import json
from decimal import Decimal
from django.db.models import Sum, Count, Q

sys.path.append('d:/PROJECTS/New_PoS')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

from apps.core.models import Tenant, Location, User
from apps.sales.models import Sale
from apps.accounting.models import CashTransfer
from django.utils import timezone
from datetime import timedelta

tenant = Tenant.objects.first()

today = timezone.now().date()
print(f"Today is {today}")

# Let's see Sales that exist
total_sales = Sale.objects.filter(tenant=tenant).count()
print(f"Total sales in DB: {total_sales}")

recent_sales = Sale.objects.filter(tenant=tenant).order_by('-created_at')[:5]
for s in recent_sales:
    print(f"Sale {s.sale_number} at {s.created_at} - Total: {s.total} - Status: {s.status}")

# Let's examine the query:
shops = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True)

start_date = today
end_date = today

def get_date_filter(field_name='created_at__date'):
    q = Q()
    if start_date:
        q &= Q(**{f'{field_name}__gte': start_date})
    if end_date:
        q &= Q(**{f'{field_name}__lte': end_date})
    return q

for shop in shops:
    print(f"\n--- Checking {shop.name} ---")
    
    # Simple count
    all_shop_sales = Sale.objects.filter(tenant=tenant, shop=shop)
    print(f"Total sales all time for this shop: {all_shop_sales.count()}")
    
    complete_sales = Sale.objects.filter(tenant=tenant, shop=shop, status='COMPLETED')
    print(f"Completed sales all time for this shop: {complete_sales.count()}")
    
    filtered_sales = Sale.objects.filter(Q(tenant=tenant, shop=shop, status='COMPLETED') & get_date_filter())
    print(f"Filtered sales (TODAY) for this shop: {filtered_sales.count()}")
