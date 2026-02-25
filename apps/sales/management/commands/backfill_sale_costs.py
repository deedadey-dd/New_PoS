"""
Management command to backfill unit_cost on SaleItems that have cost = 0.
Finds the best-guess batch at the sale's shop location for each product.
"""
from django.core.management.base import BaseCommand
from decimal import Decimal

from apps.sales.models import SaleItem
from apps.inventory.models import Batch


class Command(BaseCommand):
    help = 'Backfill unit_cost on SaleItems where cost is 0 but batches exist'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--tenant-id',
            type=int,
            help='Only backfill for a specific tenant',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        tenant_id = options.get('tenant_id')

        # Find all completed SaleItems with unit_cost = 0
        qs = SaleItem.objects.filter(
            sale__status='COMPLETED',
            unit_cost=Decimal('0'),
        ).select_related('sale__shop', 'product', 'batch')

        if tenant_id:
            qs = qs.filter(sale__tenant_id=tenant_id)

        total = qs.count()
        updated = 0
        skipped = 0

        self.stdout.write(f"Found {total} SaleItems with unit_cost = 0")

        for item in qs.iterator():
            shop = item.sale.shop
            product = item.product

            # Try to find the best batch for this product at this shop
            batch = Batch.objects.filter(
                tenant=item.sale.tenant,
                product=product,
                location=shop,
            ).exclude(
                unit_cost=Decimal('0'),
            ).order_by('expiry_date', 'created_at').first()

            if not batch:
                # Try any batch for this product tenant-wide
                batch = Batch.objects.filter(
                    tenant=item.sale.tenant,
                    product=product,
                ).exclude(
                    unit_cost=Decimal('0'),
                ).order_by('expiry_date', 'created_at').first()

            if batch:
                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] Would set {product.name} "
                        f"(Sale {item.sale.sale_number}) "
                        f"unit_cost = {batch.unit_cost}"
                    )
                else:
                    item.unit_cost = batch.unit_cost
                    if not item.batch:
                        item.batch = batch
                    # Use update() to bypass the save() total recalc
                    SaleItem.objects.filter(pk=item.pk).update(
                        unit_cost=batch.unit_cost,
                        batch=item.batch,
                    )
                updated += 1
            else:
                skipped += 1

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"\n{action} {updated} SaleItems, skipped {skipped} (no batch found)"
        ))
