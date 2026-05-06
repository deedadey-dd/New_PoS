from apps.sales.models import Sale, Shift
from apps.core.models import User
from django.db.models import Sum
from decimal import Decimal

print('=== ROOT CAUSE: Who owns the shift on sales where cashier=manager? ===\n')

for mgr in User.objects.filter(role__name__in=['SHOP_MANAGER','SHOP_CASHIER'], is_active=True, tenant__isnull=False).select_related('tenant','role'):
    tenant = mgr.tenant

    # Shiftless as cashier
    sl = Sale.objects.filter(tenant=tenant, cashier=mgr, status='COMPLETED', shift__isnull=True, payment_method__in=['CASH','MIXED'])
    sl_total = sl.aggregate(t=Sum('total'))['t'] or Decimal('0')

    # In-shift but shift belongs to SOMEONE ELSE (strict workflow: attendant created, manager paid)
    in_shift_others = Sale.objects.filter(
        tenant=tenant, cashier=mgr, status='COMPLETED',
        payment_method__in=['CASH','MIXED'], shift__isnull=False
    ).exclude(shift__attendant=mgr)
    in_shift_others_total = in_shift_others.aggregate(t=Sum('total'))['t'] or Decimal('0')

    # In own shifts
    own_closed_shifts = Shift.objects.filter(tenant=tenant, attendant=mgr, status='CLOSED')
    in_own_shifts = Sale.objects.filter(
        tenant=tenant, cashier=mgr, status='COMPLETED',
        payment_method__in=['CASH','MIXED'], shift__attendant=mgr, shift__status='CLOSED'
    )
    in_own_shifts_total = in_own_shifts.aggregate(t=Sum('total'))['t'] or Decimal('0')

    open_shift = Shift.objects.filter(tenant=tenant, attendant=mgr, status='OPEN').first()
    in_open_shift = Decimal('0')
    if open_shift:
        cs = open_shift.sales.filter(status='COMPLETED', payment_method__in=['CASH','MIXED'])
        in_open_shift = cs.aggregate(t=Sum('total'))['t'] or Decimal('0')

    print(f'[{tenant.name}] {mgr.get_full_name() or mgr.email} ({mgr.role.name})')
    print(f'  shiftless (cashier=me, shift=NULL):          count={sl.count()}  total={sl_total}')
    print(f'  in OTHERS shifts (cashier=me, not my shift): count={in_shift_others.count()}  total={in_shift_others_total}')
    print(f'  in OWN closed shifts (shift.attendant=me):   count={in_own_shifts.count()}  total={in_own_shifts_total}')
    print(f'  in OWN open shift (live):                    total={in_open_shift}')
    print()

print('KEY INSIGHT: "shiftless" includes sales where attendant used strict workflow')
print('(sale.shift = attendant shift, not null, cashier=manager) - these are CORRECTLY excluded from shiftless query.')
print('But "in_others_shifts" sales are from strict-workflow where manager is cashier but DID NOT create the shift.')
print('These have shift!=NULL and are NOT covered by manager self-transfers.')
