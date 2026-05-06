import django, os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'pos_system.settings'
sys.path.insert(0, '.')
django.setup()

from apps.core.models import User, Tenant
from apps.sales.models import Shift, Sale
from apps.accounting.models import CashTransfer
from django.db.models import Sum
from decimal import Decimal

print('=== Non-strict tenants and their shop manager status ===\n')

non_strict = Tenant.objects.filter(use_strict_sales_workflow=False)
for tenant in non_strict:
    print(f'Tenant: {tenant.name}')
    print(f'  Strict workflow: {tenant.use_strict_sales_workflow}')
    managers = User.objects.filter(tenant=tenant, role__name__in=['SHOP_MANAGER','SHOP_CASHIER'], is_active=True).select_related('role')
    for mgr in managers:
        received = CashTransfer.objects.filter(tenant=tenant, to_user=mgr, status='CONFIRMED').aggregate(t=Sum('amount'))['t'] or Decimal('0')
        sent = CashTransfer.objects.filter(tenant=tenant, from_user=mgr, status__in=['PENDING','CONFIRMED']).exclude(to_user=mgr).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        self_t = CashTransfer.objects.filter(tenant=tenant, from_user=mgr, to_user=mgr, status='CONFIRMED').count()
        open_shift = Shift.objects.filter(tenant=tenant, attendant=mgr, status='OPEN').first()
        closed_shifts = Shift.objects.filter(tenant=tenant, attendant=mgr, status='CLOSED').count()
        total_sales = Sale.objects.filter(tenant=tenant, cashier=mgr, status='COMPLETED', payment_method__in=['CASH','MIXED']).count()
        print(f'  {mgr.get_full_name() or mgr.email} ({mgr.role.name}):')
        print(f'    received={received}  sent={sent}  self_transfers={self_t}')
        print(f'    open_shift={"#"+str(open_shift.pk) if open_shift else "none"}  closed_shifts={closed_shifts}  cash_sales={total_sales}')
        print(f'    email: {mgr.email}')
    print()
