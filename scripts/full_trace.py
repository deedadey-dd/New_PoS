"""
Full end-to-end diagnostic matching EXACTLY what forms.py and context_processors.py compute.
Run with: python manage.py shell < scripts/full_trace.py
"""
from apps.sales.models import Sale, Shift
from apps.accounting.models import CashTransfer, ExpenditureItem
from apps.customers.models import CustomerTransaction
from apps.core.models import User
from django.db.models import Sum
from decimal import Decimal

SEP = "-" * 70

def compute_coh(user):
    """Exact replica of context_processors.py SHOP_MANAGER/SHOP_CASHIER block."""
    tenant = user.tenant

    received = CashTransfer.objects.filter(
        tenant=tenant, to_user=user, status='CONFIRMED'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    sent = CashTransfer.objects.filter(
        tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
    ).exclude(to_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    open_shift = Shift.objects.filter(tenant=tenant, attendant=user, status='OPEN').first()
    current_shift_cash = Decimal('0')
    if open_shift:
        cs = open_shift.sales.filter(status='COMPLETED')
        shift_cash = cs.filter(payment_method='CASH').aggregate(total=Sum('total'))['total'] or Decimal('0')
        shift_mixed = cs.filter(payment_method='MIXED').aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
        current_shift_cash = shift_cash + shift_mixed
        if tenant.include_opening_cash_in_transfer:
            current_shift_cash += open_shift.opening_cash

    last_transfer_dt = CashTransfer.objects.filter(
        tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
    ).exclude(to_user=user).order_by('-created_at').values_list('created_at', flat=True).first()

    shiftless_filter = dict(tenant=tenant, cashier=user, status='COMPLETED', shift__isnull=True)
    if last_transfer_dt:
        shiftless_filter['created_at__gt'] = last_transfer_dt

    shiftless_cash = Sale.objects.filter(**shiftless_filter, payment_method='CASH').aggregate(total=Sum('total'))['total'] or Decimal('0')
    shiftless_mixed = Sale.objects.filter(**shiftless_filter, payment_method='MIXED').aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
    shiftless_sales = shiftless_cash + shiftless_mixed

    customer_payments = CustomerTransaction.objects.filter(
        tenant=tenant, performed_by=user, transaction_type='CREDIT',
        description__icontains='(CASH)'
    ).exclude(description__icontains='ECASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')

    expenses = ExpenditureItem.objects.filter(
        request__tenant=tenant, request__requested_by=user,
        status='APPROVED', source_of_funds='SHOP_CASH'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    coh = max(Decimal('0'), received - sent + current_shift_cash + shiftless_sales + customer_payments - expenses)

    return {
        'received': received,
        'self_transfers': CashTransfer.objects.filter(tenant=tenant, from_user=user, to_user=user, status='CONFIRMED').aggregate(total=Sum('amount'))['total'] or Decimal('0'),
        'sent': sent,
        'current_shift_cash': current_shift_cash,
        'shiftless_sales': shiftless_sales,
        'customer_payments': customer_payments,
        'expenses': expenses,
        'coh': coh,
        'last_transfer_dt': last_transfer_dt,
        'open_shift': open_shift,
    }

print(SEP)
print("  EXACT FORMULA TRACE (mirrors context_processors.py)")
print(SEP)

for user in User.objects.filter(
    role__name__in=['SHOP_MANAGER', 'SHOP_CASHIER'],
    is_active=True, tenant__isnull=False
).select_related('role', 'tenant').order_by('tenant', 'role__name'):
    r = compute_coh(user)
    print(f"\n[{user.tenant.name}] {user.get_full_name() or user.email} ({user.role.name})")
    print(f"  received (incl self={r['self_transfers']:>10.2f}): {r['received']:>10.2f}")
    print(f"  sent_upstream:                       {r['sent']:>10.2f}")
    print(f"  current_shift_cash:                  {r['current_shift_cash']:>10.2f}  {'(open shift: #' + str(r['open_shift'].pk) + ')' if r['open_shift'] else ''}")
    print(f"  shiftless_sales (since {r['last_transfer_dt'].strftime('%Y-%m-%d') if r['last_transfer_dt'] else 'ALL TIME'}): {r['shiftless_sales']:>10.2f}")
    print(f"  customer_payments:                   {r['customer_payments']:>10.2f}")
    print(f"  expenses:                            {r['expenses']:>10.2f}")
    print(f"  ─────────────────────────────────────────────")
    print(f"  CASH ON HAND:                        {r['coh']:>10.2f}")

print(f"\n{SEP}")
