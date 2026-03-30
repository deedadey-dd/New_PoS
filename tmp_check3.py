import os
import sys
import django

sys.path.append('d:/PROJECTS/New_PoS')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

from apps.sales.models import Sale
from apps.core.models import Tenant
from django.utils import timezone

tenant = Tenant.objects.get(name="Dee Express")
print("Total sales for Dee Express:", Sale.objects.filter(tenant=tenant).count())
sales = Sale.objects.filter(tenant=tenant).order_by('-created_at')[:5]
for s in sales:
    print(s.created_at, s.status, s.total)

# Check today filter
today = timezone.now().date()
print(f"Today is {today}")
print("Sales today:", Sale.objects.filter(tenant=tenant, created_at__date__gte=today).count())

q = django.db.models.Q()
q &= django.db.models.Q(**{'created_at__date__gte': today})
q &= django.db.models.Q(**{'created_at__date__lte': today})
print("Sales exactly today (Auditor filter):", Sale.objects.filter(django.db.models.Q(tenant=tenant, status='COMPLETED') & q).count())
