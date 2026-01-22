"""
Management command to check tenant subscriptions and handle expiry notifications.
Run this command daily via cron job: python manage.py check_subscriptions

Notification logic:
    - 5 days before expiry: Start daily notifications
    - Up to 10 days after expiry: Continue daily notifications
    - After 10 days post-expiry: Deactivate tenant (read-only mode)
    - After 6 months inactive without superadmin note: Lock account
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from apps.core.models import Tenant, User
from apps.notifications.models import Notification
from apps.subscriptions.models import SubscriptionNotificationLog
from apps.subscriptions.services.notification_service import NotificationService
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check tenant subscriptions, send notifications, and handle expirations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--skip-notifications',
            action='store_true',
            help='Skip sending external notifications (email/SMS)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        skip_notifications = options['skip_notifications']
        today = timezone.now().date()
        
        self.stdout.write(f"Checking subscriptions as of {today}...")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be made"))
        
        # Process different subscription states
        self.process_trial_expirations(today, dry_run, skip_notifications)
        self.process_expiry_warnings(today, dry_run, skip_notifications)
        self.process_expired_subscriptions(today, dry_run, skip_notifications)
        self.process_deactivations(today, dry_run, skip_notifications)
        self.process_lockouts(today, dry_run, skip_notifications)
        
        self.stdout.write(self.style.SUCCESS("\nSubscription check complete."))

    def process_trial_expirations(self, today, dry_run, skip_notifications):
        """Deactivate trial accounts after 14 days."""
        self.stdout.write("\n--- Checking for trial expirations (14 days) ---")
        
        trial_expiry_date = today - timedelta(days=14)
        
        # Find TRIAL tenants that started more than 14 days ago
        tenants = Tenant.objects.filter(
            subscription_status='TRIAL',
            subscription_start_date__lte=trial_expiry_date,
            is_active=True
        )
        
        count = tenants.count()
        if count == 0:
            self.stdout.write("  No trial accounts to expire.")
            return
        
        self.stdout.write(f"  Found {count} trial account(s) to expire:")
        
        for tenant in tenants:
            days_in_trial = (today - tenant.subscription_start_date).days
            self.stdout.write(f"    - {tenant.name} (trial for {days_in_trial} days)")
            
            if not dry_run:
                # Set to INACTIVE
                tenant.subscription_status = 'INACTIVE'
                tenant.is_active = False
                tenant.subscription_end_date = today
                tenant.last_notification_sent = today
                tenant.save(update_fields=['subscription_status', 'is_active', 'subscription_end_date', 'last_notification_sent'])
                
                # Send notification
                if not skip_notifications:
                    success, channel, error = NotificationService.send_subscription_notification(
                        tenant, 'trial_expired', days_in_trial
                    )
                    if success:
                        self.stdout.write(self.style.SUCCESS(f"      Trial expiration notification sent via {channel}"))
                    
                    self._log_notification(tenant, 'TRIAL_EXPIRED', channel, success, error)
                
                # Create in-app notification
                self._create_inapp_notification(
                    tenant,
                    'Trial Period Ended',
                    'Your 14-day trial has ended. Please subscribe to continue using the service.',
                    'TRIAL_EXPIRED'
                )
                
                logger.info(f"Tenant '{tenant.name}' trial expired after {days_in_trial} days")

    def process_expiry_warnings(self, today, dry_run, skip_notifications):
        """Send warnings 5 days before expiry."""
        self.stdout.write("\n--- Checking for expiry warnings (5 days before) ---")
        
        warning_date = today + timedelta(days=5)
        tenants = Tenant.objects.filter(
            subscription_status__in=['ACTIVE', 'TRIAL'],
            subscription_end_date__lte=warning_date,
            subscription_end_date__gt=today,
            auto_renew=False,
            is_active=True
        ).exclude(
            last_notification_sent=today  # Don't notify if already notified today
        )
        
        count = tenants.count()
        if count == 0:
            self.stdout.write("  No tenants need expiry warnings.")
            return
        
        self.stdout.write(f"  Found {count} tenant(s) expiring within 5 days:")
        
        for tenant in tenants:
            days_left = (tenant.subscription_end_date - today).days
            self.stdout.write(f"    - {tenant.name} (expires in {days_left} days)")
            
            if not dry_run:
                # Send notification
                if not skip_notifications:
                    success, channel, error = NotificationService.send_subscription_notification(
                        tenant, 'expiry_warning', days_left
                    )
                    if success:
                        self.stdout.write(self.style.SUCCESS(f"      Notification sent via {channel}"))
                        self._log_notification(tenant, 'EXPIRY_WARNING', channel, success)
                    else:
                        self.stdout.write(self.style.WARNING(f"      Notification failed: {error}"))
                        self._log_notification(tenant, 'EXPIRY_WARNING', channel, False, error)
                
                # Create in-app notification
                self._create_inapp_notification(
                    tenant, 
                    'Subscription Expiring Soon',
                    f'Your subscription expires in {days_left} days. Please renew to avoid service interruption.',
                    'SUBSCRIPTION_EXPIRY'
                )
                
                # Update last notification date
                tenant.last_notification_sent = today
                tenant.save(update_fields=['last_notification_sent'])

    def process_expired_subscriptions(self, today, dry_run, skip_notifications):
        """Process subscriptions that have expired (up to 10 days ago)."""
        self.stdout.write("\n--- Checking expired subscriptions (up to 10 days) ---")
        
        ten_days_ago = today - timedelta(days=10)
        tenants = Tenant.objects.filter(
            subscription_status__in=['ACTIVE', 'TRIAL'],
            subscription_end_date__lt=today,
            subscription_end_date__gte=ten_days_ago,
            auto_renew=False
        ).exclude(
            last_notification_sent=today
        )
        
        count = tenants.count()
        if count == 0:
            self.stdout.write("  No recently expired subscriptions.")
            return
        
        self.stdout.write(f"  Found {count} recently expired subscription(s):")
        
        for tenant in tenants:
            days_expired = (today - tenant.subscription_end_date).days
            self.stdout.write(f"    - {tenant.name} (expired {days_expired} days ago)")
            
            if not dry_run:
                # Update status to EXPIRED
                tenant.subscription_status = 'EXPIRED'
                tenant.last_notification_sent = today
                tenant.save(update_fields=['subscription_status', 'last_notification_sent'])
                
                # Send notification
                if not skip_notifications:
                    success, channel, error = NotificationService.send_subscription_notification(
                        tenant, 'expired', days_expired
                    )
                    if success:
                        self.stdout.write(self.style.SUCCESS(f"      Notification sent via {channel}"))
                    
                    self._log_notification(tenant, 'EXPIRED', channel, success, error)
                
                # Create in-app notification
                self._create_inapp_notification(
                    tenant,
                    'Subscription Expired',
                    f'Your subscription expired {days_expired} days ago. Please renew to restore full access.',
                    'SUBSCRIPTION_EXPIRY'
                )
                
                logger.info(f"Tenant '{tenant.name}' marked as EXPIRED")

    def process_deactivations(self, today, dry_run, skip_notifications):
        """Deactivate tenants more than 10 days past expiry."""
        self.stdout.write("\n--- Checking for deactivations (10+ days expired) ---")
        
        cutoff_date = today - timedelta(days=10)
        tenants = Tenant.objects.filter(
            subscription_status='EXPIRED',
            subscription_end_date__lt=cutoff_date,
            is_active=True,
            auto_renew=False
        )
        
        count = tenants.count()
        if count == 0:
            self.stdout.write("  No tenants need deactivation.")
            return
        
        self.stdout.write(f"  Found {count} tenant(s) to deactivate:")
        
        for tenant in tenants:
            days_expired = (today - tenant.subscription_end_date).days
            self.stdout.write(f"    - {tenant.name} (expired {days_expired} days ago)")
            
            if not dry_run:
                # Set to INACTIVE (can login but cannot transact)
                tenant.subscription_status = 'INACTIVE'
                tenant.last_notification_sent = today
                tenant.save(update_fields=['subscription_status', 'last_notification_sent'])
                
                # Send notification
                if not skip_notifications:
                    success, channel, error = NotificationService.send_subscription_notification(
                        tenant, 'deactivated', days_expired
                    )
                    if success:
                        self.stdout.write(self.style.SUCCESS(f"      Deactivation notification sent via {channel}"))
                    
                    self._log_notification(tenant, 'DEACTIVATED', channel, success, error)
                
                # Create in-app notification
                self._create_inapp_notification(
                    tenant,
                    'Account Deactivated',
                    'Your account has been deactivated due to expired subscription. You can still login but cannot perform transactions.',
                    'SUBSCRIPTION_DEACTIVATED'
                )
                
                logger.warning(f"Tenant '{tenant.name}' DEACTIVATED due to expired subscription")

    def process_lockouts(self, today, dry_run, skip_notifications):
        """Lock accounts that have been inactive for 6 months without superadmin intervention."""
        self.stdout.write("\n--- Checking for 6-month lockouts ---")
        
        # 6 months ago
        lockout_date = today - timedelta(days=180)
        
        # Find INACTIVE tenants from 6+ months ago without recent admin_notes update
        tenants = Tenant.objects.filter(
            subscription_status='INACTIVE',
            subscription_end_date__lt=lockout_date,
            locked_at__isnull=True
        ).exclude(
            # Exclude if admin_notes has been updated recently (check via updated_at)
            updated_at__gte=timezone.now() - timedelta(days=30)
        )
        
        # Additional check: only lock if no superadmin comment in the last 30 days
        tenants_to_lock = []
        for tenant in tenants:
            # Check if admin_notes is empty or hasn't been updated
            if not tenant.admin_notes.strip():
                tenants_to_lock.append(tenant)
            else:
                # Skip if there's a recent admin note
                self.stdout.write(f"    - {tenant.name} skipped (has admin notes)")
        
        count = len(tenants_to_lock)
        if count == 0:
            self.stdout.write("  No tenants need lockout.")
            return
        
        self.stdout.write(f"  Found {count} tenant(s) to lock:")
        
        for tenant in tenants_to_lock:
            months_inactive = (today - tenant.subscription_end_date).days // 30
            self.stdout.write(f"    - {tenant.name} (inactive for ~{months_inactive} months)")
            
            if not dry_run:
                # Lock the account
                tenant.subscription_status = 'LOCKED'
                tenant.locked_at = timezone.now()
                tenant.is_active = False
                tenant.save(update_fields=['subscription_status', 'locked_at', 'is_active'])
                
                # Send notification
                if not skip_notifications:
                    success, channel, error = NotificationService.send_subscription_notification(
                        tenant, 'locked', months_inactive
                    )
                    if success:
                        self.stdout.write(self.style.SUCCESS(f"      Lock notification sent via {channel}"))
                    
                    self._log_notification(tenant, 'LOCKED', channel, success, error)
                
                # Create in-app notification for any active admin users
                self._create_inapp_notification(
                    tenant,
                    'Account Locked',
                    'Your account has been locked due to 6 months of inactivity. Please contact support to unlock.',
                    'ACCOUNT_LOCKED'
                )
                
                logger.error(f"Tenant '{tenant.name}' LOCKED due to 6-month inactivity")

    def _create_inapp_notification(self, tenant, title, message, notification_type):
        """Create in-app notification for tenant admins."""
        admins = User.objects.filter(
            tenant=tenant,
            role__name='ADMIN',
            is_active=True
        )
        
        for admin in admins:
            Notification.objects.create(
                tenant=tenant,
                user=admin,
                title=title,
                message=message,
                notification_type=notification_type,
                reference_type='Tenant',
                reference_id=tenant.id
            )

    def _log_notification(self, tenant, notification_type, channel, is_sent, error=None):
        """Log notification to database."""
        try:
            log = SubscriptionNotificationLog(
                tenant=tenant,
                notification_type=notification_type,
                channel=channel or 'NONE',
                is_sent=is_sent,
                error_message=error or ''
            )
            
            # Get admin contact info
            admin = User.objects.filter(
                tenant=tenant,
                role__name='ADMIN',
                is_active=True
            ).first()
            
            if admin:
                log.recipient_email = admin.email
                log.recipient_phone = admin.phone
            
            if is_sent:
                log.sent_at = timezone.now()
            
            log.save()
        except Exception as e:
            logger.error(f"Failed to log notification: {e}")
