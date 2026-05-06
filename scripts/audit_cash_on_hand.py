"""
audit_cash_on_hand.py
=====================
Read-only diagnostic script. Run on production to detect:

  1. Self-transfer double-count:
     CashTransfer rows where from_user == to_user (manager's own shift close)
     These were being double-counted: once in `received` and again in `own_sales`.

  2. Cash-on-hand discrepancy between the old formula and the corrected formula
     for every active SHOP_MANAGER and SHOP_CASHIER.

Usage (on production server):
    source venv/bin/activate
    python manage.py shell < scripts/audit_cash_on_hand.py

    OR pipe the output to a file:
    python manage.py shell < scripts/audit_cash_on_hand.py > audit_results.txt 2>&1
"""
import os
import sys
import django

# ── bootstrap (needed when run as a plain script, not via manage.py shell) ───
if 'DJANGO_SETTINGS_MODULE' not in os.environ:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
    django.setup()

from decimal import Decimal
from django.db.models import Sum
from apps.accounting.models import CashTransfer, ExpenditureItem
from apps.sales.models import Sale, Shift
from apps.customers.models import CustomerTransaction
from apps.core.models import Tenant, User

SEP = "─" * 72
print(SEP)
print("  CASH-ON-HAND AUDIT REPORT")
print(SEP)

# ─────────────────────────────────────────────────────────────────────────────
# Part 1: Self-transfer double-count records
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] SELF-TRANSFER RECORDS (manager shift-close deposits)\n")
print("    These were double-counted in the old formula (in both `received` and `own_sales`).\n")

self_transfers = CashTransfer.objects.filter(
    from_user__isnull=False,
    to_user__isnull=False,
).extra(where=["from_user_id = to_user_id"]).select_related('from_user', 'tenant').order_by('tenant', '-created_at')

if not self_transfers.exists():
    print("    ✓ No self-transfer records found.\n")
else:
    current_tenant = None
    tenant_total = Decimal('0')
    grand_total = Decimal('0')
    for t in self_transfers:
        if current_tenant != t.tenant_id:
            if current_tenant is not None:
                print(f"      Tenant subtotal: {tenant_total:.2f}\n")
            current_tenant = t.tenant_id
            tenant_total = Decimal('0')
            print(f"    Tenant: {t.tenant.name}")
            print(f"    {'ID':<6} {'Date':<22} {'User':<30} {'Amount':>12} {'Status':<12} Notes")
            print(f"    {'─'*6} {'─'*22} {'─'*30} {'─'*12} {'─'*12} {'─'*30}")
        user_label = t.from_user.get_full_name() or t.from_user.email
        print(f"    {t.pk:<6} {str(t.created_at.strftime('%Y-%m-%d %H:%M')):<22} {user_label:<30} {t.amount:>12.2f} {t.status:<12} {t.notes[:40]}")
        tenant_total += t.amount
        grand_total += t.amount
    print(f"      Tenant subtotal: {tenant_total:.2f}\n")
    print(f"    Grand total of self-transfer amounts: {grand_total:.2f}")
    print(f"    Count: {self_transfers.count()} record(s)\n")

# ─────────────────────────────────────────────────────────────────────────────
# Part 2: Old formula vs new formula comparison per manager/cashier
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + SEP)
print("[2] CASH-ON-HAND: OLD vs NEW FORMULA — per SHOP_MANAGER / SHOP_CASHIER\n")
print("    A positive 'Diff' means the old formula was OVER-reporting cash-on-hand.\n")

target_roles = ['SHOP_MANAGER', 'SHOP_CASHIER']
managers = User.objects.filter(
    role__name__in=target_roles,
    is_active=True,
    tenant__isnull=False
).select_related('role', 'tenant').order_by('tenant', 'role__name', 'email')

for user in managers:
    tenant = user.tenant

    # ── OLD FORMULA ──────────────────────────────────────────────────────────
    received_old = CashTransfer.objects.filter(
        tenant=tenant, to_user=user, status='CONFIRMED'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    sent_old = CashTransfer.objects.filter(
        tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
    ).exclude(to_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    open_shift = Shift.objects.filter(tenant=tenant, attendant=user, status='OPEN').first()
    open_shift_cash_old = open_shift.opening_cash if open_shift else Decimal('0')

    own_cash_old = Sale.objects.filter(
        tenant=tenant, cashier=user, status='COMPLETED', payment_method='CASH'
    ).aggregate(total=Sum('total'))['total'] or Decimal('0')
    own_mixed_old = Sale.objects.filter(
        tenant=tenant, cashier=user, status='COMPLETED', payment_method='MIXED'
    ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')

    cust_old = CustomerTransaction.objects.filter(
        tenant=tenant, performed_by=user, transaction_type='CREDIT',
        description__icontains='(CASH)'
    ).exclude(description__icontains='ECASH').exclude(
        description__icontains='MOMO'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    exp_old = ExpenditureItem.objects.filter(
        request__tenant=tenant, request__requested_by=user,
        status='APPROVED', source_of_funds='SHOP_CASH'
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    old_coh = received_old - sent_old + open_shift_cash_old + own_cash_old + own_mixed_old + cust_old - exp_old

    # ── NEW FORMULA ──────────────────────────────────────────────────────────
    received_new = CashTransfer.objects.filter(
        tenant=tenant, to_user=user, status='CONFIRMED'
    ).exclude(from_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    sent_new = CashTransfer.objects.filter(
        tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
    ).exclude(to_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')

    last_transfer_dt = CashTransfer.objects.filter(
        tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
    ).exclude(to_user=user).order_by('-created_at').values_list('created_at', flat=True).first()

    sales_filter = dict(tenant=tenant, cashier=user, status='COMPLETED')
    if last_transfer_dt:
        sales_filter['created_at__gt'] = last_transfer_dt

    own_cash_new = Sale.objects.filter(**sales_filter, payment_method='CASH').aggregate(total=Sum('total'))['total'] or Decimal('0')
    own_mixed_new = Sale.objects.filter(**sales_filter, payment_method='MIXED').aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')

    open_shift_cash_new = (open_shift.opening_cash if open_shift and tenant.include_opening_cash_in_transfer else Decimal('0'))

    new_coh = max(Decimal('0'), received_new - sent_new + open_shift_cash_new + own_cash_new + own_mixed_new + cust_old - exp_old)

    diff = old_coh - new_coh
    flag = " ← DISCREPANCY" if abs(diff) > Decimal('0.01') else ""

    print(f"  [{tenant.name}] {user.get_full_name() or user.email} ({user.role.name})")
    print(f"    Old formula: {old_coh:>12.2f}   New formula: {new_coh:>12.2f}   Diff: {diff:>10.2f}{flag}")
    if last_transfer_dt:
        print(f"    Last upstream transfer at: {last_transfer_dt.strftime('%Y-%m-%d %H:%M')}")
    print()

print(SEP)
print("  END OF REPORT — no data was modified.")
print(SEP)
