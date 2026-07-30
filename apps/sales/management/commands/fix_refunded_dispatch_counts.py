"""
Management command: fix_refunded_dispatch_counts

One-time data correction for sales that were refunded BEFORE the fix that
resets dispatched_quantity on SaleItems was deployed.

These sales have status='REFUNDED' but still carry non-zero dispatched_quantity
on their SaleItems (and possibly is_dispatched=True on the Sale itself),
which inflates dispatched-goods counts in end-of-day reports.

Usage:
    # Preview what will be fixed (no changes written)
    python manage.py fix_refunded_dispatch_counts --dry-run

    # Apply the fix
    python manage.py fix_refunded_dispatch_counts
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = (
        "Reset dispatched_quantity on SaleItems and clear dispatch flags on Sales "
        "that have already been refunded. Safe to run multiple times (idempotent)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Preview affected records without making any changes.',
        )

    def handle(self, *args, **options):
        from apps.sales.models import Sale, SaleItem
        from decimal import Decimal

        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be written.\n'))

        # Find refunded sales that still have non-zero dispatched_quantity on any item
        # OR that still have is_dispatched=True on the sale record itself.
        affected_sales = Sale.objects.filter(
            status='REFUNDED'
        ).filter(
            # At least one item with dispatched_quantity > 0 OR sale-level flag still set
            models_Q(items__dispatched_quantity__gt=Decimal('0')) |
            models_Q(is_dispatched=True)
        ).distinct().select_related('shop', 'tenant')

        total_sales = affected_sales.count()

        if total_sales == 0:
            self.stdout.write(self.style.SUCCESS(
                'No affected refunded sales found. Nothing to fix.'
            ))
            return

        self.stdout.write(
            f'Found {total_sales} refunded sale(s) with stale dispatch data:\n'
        )

        sales_fixed = 0
        items_fixed = 0

        for sale in affected_sales:
            # Collect stale items for this sale
            stale_items = list(
                sale.items.filter(dispatched_quantity__gt=Decimal('0'))
            )
            stale_item_count = len(stale_items)

            self.stdout.write(
                f'  Sale {sale.sale_number} (tenant: {sale.tenant}, shop: {sale.shop.name})'
                f' — {stale_item_count} item(s) with dispatched_quantity > 0'
                f', is_dispatched={sale.is_dispatched}'
            )

            if not dry_run:
                with transaction.atomic():
                    # Reset all items for this sale
                    sale.items.filter(dispatched_quantity__gt=Decimal('0')).update(
                        dispatched_quantity=Decimal('0')
                    )

                    # Clear sale-level dispatch flags
                    fields_to_update = []
                    if sale.is_dispatched:
                        sale.is_dispatched = False
                        fields_to_update.append('is_dispatched')
                    if sale.dispatched_at is not None:
                        sale.dispatched_at = None
                        fields_to_update.append('dispatched_at')
                    if sale.dispatched_by_id is not None:
                        sale.dispatched_by = None
                        fields_to_update.append('dispatched_by')
                    if fields_to_update:
                        sale.save(update_fields=fields_to_update)

            sales_fixed += 1
            items_fixed += stale_item_count

        self.stdout.write('')
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN complete. Would fix {sales_fixed} sale(s) and {items_fixed} item(s).\n'
                f'Run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Done. Fixed {sales_fixed} sale(s) and reset {items_fixed} SaleItem(s).'
            ))


# Alias so Django ORM Q objects work even though we import late
from django.db.models import Q as models_Q
