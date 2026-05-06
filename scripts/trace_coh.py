import django
import os
import sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'pos_system.settings'
sys.path.insert(0, '.')
django.setup()

from apps.sales.models import Sale, Shift
from apps.accounting.models import CashTransfer, ExpenditureItem
from apps.customers.models import CustomerTransaction
from apps.core.models import User
from django.db.models import Sum
from decimal import Decimal

SEP = '-' * 68

def compute_coh(user):
    tenant = user.tenant
    received = CashTransfer.objects.filter(
        tenant=tenant, to_user=user, status='CONFIRMED'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    self_t = CashTransfer.objects.filter(
        tenant=tenant, from_user=user, to_user=user, status='CONFIRMED'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    sent = CashTransfer.objects.filter(
        tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
    ).exclude(to_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    open_shift = Shift.objects.filter(
        tenant=tenant, attendant=user, status='OPEN'
    ).first()
    
    current_shift_cash = Decimal('0')
    if open_shift:
        cs = open_shift.sales.filter(status='COMPLETED')
        cash_part = cs.filter(payment_method='CASH').aggregate(t=Sum('total'))['t'] or Decimal('0')
        mixed_part = cs.filter(payment_method='MIXED').aggregate(t=Sum('amount_paid'))['t'] or Decimal('0')
        current_shift_cash = cash_part + mixed_part
        if tenant.include_opening_cash_in_transfer:
            current_shift_cash += open_shift.opening_cash

    last_dt = CashTransfer.objects.filter(
        tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
    ).exclude(to_user=user).order_by('-created_at').values_list('created_at', flat=True).first()

    sf = dict(tenant=tenant, cashier=user, status='COMPLETED', shift__isnull=True)
    if last_dt:
        sf['created_at__gt'] = last_dt

    sl_cash = Sale.objects.filter(**sf, payment_method='CASH').aggregate(t=Sum('total'))['t'] or Decimal('0')
    sl_mixed = Sale.objects.filter(**sf, payment_method='MIXED').aggregate(t=Sum('amount_paid'))['t'] or Decimal('0')
    shiftless = sl_cash + sl_mixed

    cust = CustomerTransaction.objects.filter(
        tenant=tenant, performed_by=user, transaction_type='CREDIT',
        description__icontains='(CASH)'
    ).exclude(description__icontains='ECASH').aggregate(t=Sum('amount'))['t'] or Decimal('0')

    exp = ExpenditureItem.objects.filter(
        request__tenant=tenant, request__requested_by=user,
        status='APPROVED', source_of_funds='SHOP_CASH'
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    coh = max(Decimal('0'), received - sent + current_shift_cash + shiftless + cust - exp)
    return received, self_t, sent, current_shift_cash, shiftless, cust, exp, coh, last_dt, open_shift


print(SEP)
print('  FORMULA TRACE: mirrors context_processors.py exactly')
print(SEP)

for user in User.objects.filter(
    role__name__in=['SHOP_MANAGER', 'SHOP_CASHIER'],
    is_active=True, tenant__isnull=False
).select_related('role', 'tenant').order_by('tenant', 'role__name'):
    rec, st, sent, shift_c, sl, cust, exp, coh, last_dt, os_ = compute_coh(user)
    label = user.get_full_name() or user.email
    last_str = last_dt.strftime('%Y-%m-%d') if last_dt else 'beginning'
    os_str = '#' + str(os_.pk) if os_ else 'none'
    print(f'[{user.tenant.name}] {label} ({user.role.name})')
    print(f'  received={rec}  (self_transfers={st})  sent_upstream={sent}')
    print(f'  open_shift={os_str}  open_shift_cash={shift_c}')
    print(f'  shiftless_since_{last_str}={sl}')
    print(f'  customer_payments={cust}  expenses={exp}')
    print(f'  --> CASH ON HAND = {coh}')
    print()
