import os
import sys
import django

sys.path.append('d:/PROJECTS/New_PoS')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

from apps.sales.models import Sale
from apps.core.models import Tenant

print(f"Total sales in DB (all tenants): {Sale.objects.count()}")
for tenant in Tenant.objects.all():
    print(f"Tenant {tenant.name} has {Sale.objects.filter(tenant=tenant).count()} sales")
