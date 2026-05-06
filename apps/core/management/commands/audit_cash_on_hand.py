"""
Management command: audit_cash_on_hand

Detects double-counted shift self-transfers and compares old vs new
cash-on-hand formula for every SHOP_MANAGER / SHOP_CASHIER.

Usage (dev or production):
    python manage.py audit_cash_on_hand

    # Redirect to file:
    python manage.py audit_cash_on_hand > audit_results.txt
"""
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db.models import Sum

from apps.accounting.models import CashTransfer, ExpenditureItem
from apps.sales.models import Sale, Shift
from apps.customers.models import CustomerTransaction
from apps.core.models import User

SEP = "-" * 72


class Command(BaseCommand):
    help = "Read-only audit of cash-on-hand calculation correctness"

    def handle(self, *args, **options):
        self.stdout.write(SEP)
        self.stdout.write("  CASH-ON-HAND AUDIT REPORT")
        self.stdout.write(SEP)

        # ─── Part 1: Self-transfer double-count records ───────────────────────
        self.stdout.write("\n[1] SELF-TRANSFER RECORDS (manager shift-close deposits)\n")
        self.stdout.write("    These were double-counted in the old formula.\n")

        self_transfers = CashTransfer.objects.filter(
            from_user__isnull=False,
            to_user__isnull=False,
        ).extra(where=["from_user_id = to_user_id"]).select_related('from_user', 'tenant').order_by('tenant', '-created_at')

        if not self_transfers.exists():
            self.stdout.write("    OK No self-transfer records found.\n")
        else:
            current_tenant = None
            tenant_total = Decimal('0')
            grand_total = Decimal('0')
            for t in self_transfers:
                if current_tenant != t.tenant_id:
                    if current_tenant is not None:
                        self.stdout.write(f"      Subtotal: {tenant_total:.2f}\n")
                    current_tenant = t.tenant_id
                    tenant_total = Decimal('0')
                    self.stdout.write(f"    Tenant: {t.tenant.name}")
                    self.stdout.write(f"    {'ID':<6} {'Date':<22} {'User':<30} {'Amount':>12} {'Status':<12} Notes")
                    self.stdout.write(f"    {'-'*6} {'-'*22} {'-'*30} {'-'*12} {'-'*12} {'-'*30}")
                user_label = t.from_user.get_full_name() or t.from_user.email
                date_str = t.created_at.strftime('%Y-%m-%d %H:%M')
                self.stdout.write(
                    f"    {t.pk:<6} {date_str:<22} {user_label:<30} "
                    f"{t.amount:>12.2f} {t.status:<12} {t.notes[:40]}"
                )
                tenant_total += t.amount
                grand_total += t.amount
            self.stdout.write(f"      Subtotal: {tenant_total:.2f}\n")
            self.stdout.write(f"    Grand total (double-counted): {grand_total:.2f}")
            self.stdout.write(f"    Count: {self_transfers.count()} record(s)\n")

        # ─── Part 2: Old vs New formula comparison ────────────────────────────
        self.stdout.write("\n" + SEP)
        self.stdout.write("[2] CASH-ON-HAND: OLD vs NEW FORMULA per SHOP_MANAGER / SHOP_CASHIER\n")
        self.stdout.write("    Positive Diff = old formula was OVER-reporting cash-on-hand.\n")

        managers = User.objects.filter(
            role__name__in=['SHOP_MANAGER', 'SHOP_CASHIER'],
            is_active=True,
            tenant__isnull=False
        ).select_related('role', 'tenant').order_by('tenant', 'role__name', 'email')

        for user in managers:
            tenant = user.tenant

            # OLD formula
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

            cust = CustomerTransaction.objects.filter(
                tenant=tenant, performed_by=user, transaction_type='CREDIT',
                description__icontains='(CASH)'
            ).exclude(description__icontains='ECASH').exclude(
                description__icontains='MOMO'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            exp = ExpenditureItem.objects.filter(
                request__tenant=tenant, request__requested_by=user,
                status='APPROVED', source_of_funds='SHOP_CASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            old_coh = received_old - sent_old + open_shift_cash_old + own_cash_old + own_mixed_old + cust - exp

            # NEW formula (matches forms.py)
            # received = all CONFIRMED transfers TO user (INCLUDES self-transfers)
            received_new = CashTransfer.objects.filter(
                tenant=tenant, to_user=user, status='CONFIRMED'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            sent_new = CashTransfer.objects.filter(
                tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
            ).exclude(to_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            # Current open shift cash (not yet in any self-transfer)
            current_shift_cash = Decimal('0')
            if open_shift:
                cs = open_shift.sales.filter(status='COMPLETED')
                s_cash = cs.filter(payment_method='CASH').aggregate(total=Sum('total'))['total'] or Decimal('0')
                s_mixed = cs.filter(payment_method='MIXED').aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                current_shift_cash = s_cash + s_mixed
                if tenant.include_opening_cash_in_transfer:
                    current_shift_cash += open_shift.opening_cash

            # Shiftless cash sales only (no shift), scoped to since last upstream transfer
            last_transfer_dt = CashTransfer.objects.filter(
                tenant=tenant, from_user=user, status__in=['PENDING', 'CONFIRMED']
            ).exclude(to_user=user).order_by('-created_at').values_list('created_at', flat=True).first()

            sf = dict(tenant=tenant, cashier=user, status='COMPLETED', shift__isnull=True)
            if last_transfer_dt:
                sf['created_at__gt'] = last_transfer_dt

            shiftless_cash = Sale.objects.filter(**sf, payment_method='CASH').aggregate(total=Sum('total'))['total'] or Decimal('0')
            shiftless_mixed = Sale.objects.filter(**sf, payment_method='MIXED').aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            shiftless_sales = shiftless_cash + shiftless_mixed

            new_coh = max(Decimal('0'), received_new - sent_new + current_shift_cash + shiftless_sales + cust - exp)
            diff = old_coh - new_coh
            flag = " <-- DISCREPANCY" if abs(diff) > Decimal('0.01') else " OK"

            self.stdout.write(f"  [{tenant.name}] {user.get_full_name() or user.email} ({user.role.name})")
            self.stdout.write(f"    Old: {old_coh:>12.2f}  New: {new_coh:>12.2f}  Diff: {diff:>10.2f}{flag}")
            if last_transfer_dt:
                self.stdout.write(f"    Last upstream transfer: {last_transfer_dt.strftime('%Y-%m-%d %H:%M')}")
            self.stdout.write("")

        self.stdout.write(SEP)
        self.stdout.write("  END OF REPORT - no data was modified.")
        self.stdout.write(SEP)
