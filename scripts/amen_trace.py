import django
import os
import sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'pos_system.settings'
sys.path.insert(0, '.')
django.setup()

from apps.sales.models import Sale, Shift
from apps.accounting.models import CashTransfer
from apps.core.models import User
from django.db.models import Sum
from decimal import Decimal

print('=== Amen Ltd / Shop Manager Ejisu - Deep Dive ===\n')

mgr = User.objects.filter(tenant__name='Amen Ltd', role__name='SHOP_MANAGER').first()
if not mgr:
    print('User not found')
    exit()

print(f'User: {mgr.get_full_name()}, id={mgr.id}, tenant={mgr.tenant.name}')
print()

print('--- All Shifts ---')
for s in Shift.objects.filter(tenant=mgr.tenant, attendant=mgr).order_by('-start_time'):
    cash_in_shift = Sale.objects.filter(shift=s, status='COMPLETED', payment_method__in=['CASH','MIXED']).aggregate(t=Sum('total'))['t'] or Decimal('0')
    print(f'  Shift #{s.pk}: status={s.status}, opening={s.opening_cash}, closing={s.closing_cash}, sales_cash={cash_in_shift}')

print()
print('--- All CashTransfers involving this manager ---')
for t in CashTransfer.objects.filter(tenant=mgr.tenant).filter(
    __import__('django').db.models.Q(from_user=mgr) | __import__('django').db.models.Q(to_user=mgr)
).order_by('-created_at'):
    print(f'  CT #{t.pk}: from={t.from_user_id} to={t.to_user_id} amount={t.amount} status={t.status} notes={t.notes[:60]}')

print()
print('--- Sales as cashier ---')
for s in Sale.objects.filter(tenant=mgr.tenant, cashier=mgr, status='COMPLETED', payment_method__in=['CASH','MIXED']).order_by('-created_at')[:10]:
    print(f'  Sale {s.sale_number}: shift={s.shift_id}, method={s.payment_method}, total={s.total}, amount_paid={s.amount_paid}')
