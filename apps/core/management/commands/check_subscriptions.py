"""
Management command to check tenant subscriptions and expire those past their end date.
Run this command daily via cron job: python manage.py check_subscriptions
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.core.models import Tenant
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check tenant subscriptions and mark expired ones'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        today = timezone.now().date()
        
        self.stdout.write(f"Checking subscriptions as of {today}...")
        
        # Find active/trial tenants with expired subscriptions that don't auto-renew
        expired_tenants = Tenant.objects.filter(
            subscription_status__in=['ACTIVE', 'TRIAL'],
            subscription_end_date__lt=today,
            auto_renew=False
        )
        
        expired_count = expired_tenants.count()
        
        if expired_count == 0:
            self.stdout.write(self.style.SUCCESS('No expired subscriptions found.'))
            return
        
        self.stdout.write(f"Found {expired_count} expired subscription(s):")
        
        for tenant in expired_tenants:
            expired_days = (today - tenant.subscription_end_date).days
            self.stdout.write(f"  - {tenant.name} (expired {expired_days} days ago)")
            
            if not dry_run:
                # Mark as expired
                old_status = tenant.subscription_status
                tenant.subscription_status = 'EXPIRED'
                tenant.is_active = False
                tenant.save()
                
                logger.info(
                    f"Tenant '{tenant.name}' (ID: {tenant.id}) subscription expired. "
                    f"Status changed from {old_status} to EXPIRED."
                )
        
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'\nDry run complete. {expired_count} tenant(s) would be marked as expired.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\n{expired_count} tenant(s) marked as expired.'
            ))
