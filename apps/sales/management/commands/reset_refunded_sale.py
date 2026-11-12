"""
Management command: reset_refunded_sale

Restores a sale that is marked as REFUNDED back to COMPLETED (or PENDING_DISPATCH),
reverses any inventory returns / customer credit reversals caused by an uncompleted or cancelled refund,
and cancels any active refund requests for the sale so that a cashier can request a refund again.

Usage:
    # Reset a specific sale by its invoice/sale number:
    python manage.py reset_refunded_sale --sale-number INV202611120001

    # Reset by primary key:
    python manage.py reset_refunded_sale --sale-id 42

    # Dry-run (preview only, no database changes written):
    python manage.py reset_refunded_sale --sale-number INV202611120001 --dry-run

    # If run without arguments, it lists all currently REFUNDED sales with their refund requests:
    python manage.py reset_refunded_sale
"""
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from apps.sales.models import Sale, RefundRequest
from apps.inventory.models import InventoryLedger
from apps.customers.models import CustomerTransaction


class Command(BaseCommand):
    help = (
        "Reverts a sale's status from REFUNDED back to COMPLETED (or PENDING_DISPATCH), "
        "reconciles inventory and customer credit, and cancels uncompleted refund requests "
        "so the cashier can request a refund again."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sale-number',
            type=str,
            default='',
            help='Sale/Invoice number to reset (e.g. INV202611120001)'
        )
        parser.add_argument(
            '--sale-id',
            type=int,
            default=None,
            help='Sale primary key ID to reset'
        )
        parser.add_argument(
            '--tenant',
            type=str,
            default='',
            help='Tenant name or ID to disambiguate if multiple sales have the same number'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without modifying the database'
        )

    def handle(self, *args, **options):
        sale_number = options.get('sale_number', '').strip()
        sale_id = options.get('sale_id')
        tenant_filter = options.get('tenant', '').strip()
        dry_run = options.get('dry_run', False)

        if not sale_number and not sale_id:
            # List recently refunded sales for user visibility
            self.stdout.write(self.style.WARNING(
                "\nNo --sale-number or --sale-id provided. Scanning for REFUNDED sales...\n"
            ))
            qs = Sale.objects.filter(status='REFUNDED')
            if tenant_filter:
                qs = qs.filter(tenant__name__icontains=tenant_filter)
            refunded_sales = qs.order_by('-completed_at', '-id')[:25]
            if not refunded_sales.exists():
                self.stdout.write(self.style.SUCCESS("No sales currently have status='REFUNDED'."))
                return

            self.stdout.write(f"Found {refunded_sales.count()} recently REFUNDED sales:")
            self.stdout.write("-" * 95)
            self.stdout.write(f"{'ID':<6} {'Sale Number':<20} {'Tenant':<18} {'Shop':<18} {'Total':<10} {'Refund Requests'}")
            self.stdout.write("-" * 95)
            for s in refunded_sales:
                rr_statuses = [f"{r.refund_number}: {r.status} (payout: {r.payout_status})" for r in s.refund_requests.all()]
                rr_str = ", ".join(rr_statuses) if rr_statuses else "No RefundRequest"
                shop_name = s.shop.name if s.shop else "—"
                self.stdout.write(f"{s.id:<6} {s.sale_number:<20} {s.tenant.name[:17]:<18} {shop_name[:17]:<18} {s.tenant.currency_symbol}{s.total:<8} {rr_str}")
            self.stdout.write("-" * 95)
            self.stdout.write(self.style.NOTICE(
                "\nTo reset one of these sales, re-run with:\n"
                "    python manage.py reset_refunded_sale --sale-id <ID>\n"
                "or:\n"
                "    python manage.py reset_refunded_sale --sale-number <SALE_NUMBER>\n"
            ))
            return

        # Fetch the target sale
        if sale_id:
            try:
                sale = Sale.objects.select_related('shop', 'tenant', 'customer', 'attendant').get(pk=sale_id)
            except Sale.DoesNotExist:
                raise CommandError(f"Sale with ID {sale_id} does not exist.")
        else:
            qs = Sale.objects.select_related('shop', 'tenant', 'customer', 'attendant').filter(sale_number__iexact=sale_number)
            if tenant_filter:
                qs = qs.filter(tenant__name__icontains=tenant_filter)

            count = qs.count()
            if count == 0:
                raise CommandError(f"Sale with sale_number '{sale_number}' does not exist.")
            elif count > 1:
                self.stdout.write(self.style.ERROR(
                    f"Multiple sales ({count}) found with sale_number '{sale_number}'. Please specify --sale-id:\n"
                ))
                for s in qs:
                    self.stdout.write(f"  --sale-id {s.id:<6} | Tenant: {s.tenant.name:<18} | Shop: {s.shop.name if s.shop else '—':<15} | Status: {s.status}")
                return
            sale = qs.first()

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nProcessing Sale: {sale.sale_number} (ID: {sale.id})"))
        self.stdout.write(f"  Tenant:        {sale.tenant.name}")
        self.stdout.write(f"  Shop:          {sale.shop.name if sale.shop else '—'}")
        self.stdout.write(f"  Current Status: {sale.status}")
        self.stdout.write(f"  Total:         {sale.tenant.currency_symbol}{sale.total}")
        self.stdout.write(f"  Payment:       {sale.get_payment_method_display()}")
        self.stdout.write(f"  Customer:      {sale.customer.name if sale.customer else (sale.customer_name or 'Walk-in')}")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN MODE] No changes will be saved to the database.\n"))

        with transaction.atomic():
            # 1. Update related RefundRequests
            refund_reqs = sale.refund_requests.all()
            for rr in refund_reqs:
                if rr.status != 'CANCELLED':
                    self.stdout.write(f"  -> Setting RefundRequest {rr.refund_number} status from {rr.status} to CANCELLED")
                    if not dry_run:
                        rr.status = 'CANCELLED'
                        rr.rejection_reason = "Approval cancelled / sale reset by administrator"
                        rr.save(update_fields=['status', 'rejection_reason'])
                else:
                    self.stdout.write(f"  -> RefundRequest {rr.refund_number} is already CANCELLED")

            # 2. Determine target sale status
            if sale.tenant.use_strict_sales_workflow:
                has_undispatched = sale.items.filter(dispatched_quantity__lt=F('quantity')).exists()
                target_status = 'PENDING_DISPATCH' if has_undispatched else 'COMPLETED'
            else:
                target_status = 'COMPLETED'

            self.stdout.write(f"  -> Resetting sale.status from '{sale.status}' to '{target_status}'")
            if not dry_run:
                sale.status = target_status
                if sale.notes and 'REFUNDED' in sale.notes:
                    sale.notes = ""

            # 3. Reconcile InventoryLedger (SALE_RETURN)
            return_ledgers = InventoryLedger.objects.filter(
                reference_type='Refund',
                reference_id=sale.pk,
                transaction_type='SALE_RETURN'
            )
            if return_ledgers.exists():
                self.stdout.write(f"  -> Found {return_ledgers.count()} SALE_RETURN ledger entry/entries from prior refund.")
                for ret in return_ledgers:
                    self.stdout.write(
                        f"     Reversing return of {ret.quantity}x '{ret.product.name}' in batch '{ret.batch.batch_number if ret.batch else 'None'}'"
                    )
                    if not dry_run:
                        InventoryLedger.objects.create(
                            tenant=sale.tenant,
                            product=ret.product,
                            batch=ret.batch,
                            location=ret.location,
                            transaction_type='SALE',
                            quantity=-ret.quantity,
                            unit_cost=ret.unit_cost,
                            reference_type='RefundReset',
                            reference_id=sale.pk,
                            notes=f"Admin reset of prematurely refunded sale {sale.sale_number}",
                            created_by=sale.attendant
                        )
                if not dry_run:
                    # Restore item dispatched quantities and sale flag
                    for item in sale.items.all():
                        if item.dispatched_quantity == 0 and item.quantity > 0:
                            item.dispatched_quantity = item.quantity
                            item.save(update_fields=['dispatched_quantity'])
                    sale.is_dispatched = True

            # 4. Reconcile Customer Balance if credit was reversed
            if sale.customer:
                refund_credit_tx = CustomerTransaction.objects.filter(
                    customer=sale.customer,
                    reference_id=sale.sale_number,
                    transaction_type='CREDIT',
                    description__startswith="Refund:"
                ).order_by('-created_at').first()
                if refund_credit_tx:
                    self.stdout.write(
                        f"  -> Found CustomerTransaction CREDIT of {sale.tenant.currency_symbol}{refund_credit_tx.amount}. Reversing to restore customer balance."
                    )
                    if not dry_run:
                        balance_before = sale.customer.current_balance
                        sale.customer.current_balance += refund_credit_tx.amount
                        sale.customer.save(update_fields=['current_balance'])
                        CustomerTransaction.objects.create(
                            tenant=sale.tenant,
                            customer=sale.customer,
                            transaction_type='DEBIT',
                            amount=refund_credit_tx.amount,
                            reference_id=sale.sale_number,
                            balance_before=balance_before,
                            balance_after=sale.customer.current_balance,
                            description=f"Admin reset of refund: {sale.sale_number}",
                            performed_by=sale.attendant
                        )

            if not dry_run:
                sale.save()

            if dry_run:
                # Rollback dry run transaction
                transaction.set_rollback(True)

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\n[DRY RUN COMPLETE] Sale {sale.sale_number} would be successfully reset to '{target_status}'.\n"
                f"Run without --dry-run to apply these changes."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nSUCCESS: Sale {sale.sale_number} has been reset to '{target_status}'.\n"
                f"The cashier can now see the 'Refund' button and request a refund again."
            ))
