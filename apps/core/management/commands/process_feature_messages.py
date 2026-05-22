import logging
import random
from datetime import datetime, date
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.template.loader import render_to_string
from django.core.mail import send_mail

from apps.core.models import Tenant, User, FeatureMessage, FeatureIntroHistory

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Process monthly feature introduction messages (First Tuesday of the month).'

    def handle(self, *args, **options):
        today = timezone.now().date()
        
        # Check if today is the first Tuesday of the month
        # weekday() returns 0 for Monday, 1 for Tuesday.
        # If it's Tuesday AND the day of the month is between 1 and 7, it's the first Tuesday.
        if today.weekday() != 1 or today.day > 7:
            self.stdout.write(f"Today ({today}) is not the first Tuesday of the month. Exiting.")
            return

        self.stdout.write(f"Today is the first Tuesday! Processing feature messages...")
        
        # Get all active feature messages
        active_features = list(FeatureMessage.objects.filter(is_active=True))
        if not active_features:
            self.stdout.write("No active feature messages found in the pool. Exiting.")
            return
            
        # Get all active tenants
        tenants = Tenant.objects.filter(is_active=True)
        emails_sent = 0
        
        platform_url = getattr(settings, 'PLATFORM_URL', 'http://127.0.0.1:8000')
        support_email = settings.SUPPORT_EMAIL
        sales_email = getattr(settings, 'SALES_EMAIL', 'sales@hendaxis.com')
        
        for tenant in tenants:
            # Which features has this tenant already seen?
            seen_features_ids = FeatureIntroHistory.objects.filter(tenant=tenant).values_list('feature_message_id', flat=True)
            
            # Filter available features
            available_features = [f for f in active_features if f.id not in seen_features_ids]
            
            if not available_features:
                self.stdout.write(f"Tenant {tenant.name} has already received all active feature messages.")
                continue
                
            # Pick a random feature to introduce
            selected_feature = random.choice(available_features)
            
            # Send to Tenant Admins
            target_users = User.objects.filter(tenant=tenant, is_active=True, role__name='ADMIN')
            
            if target_users.exists():
                recipient_list = [u.email for u in target_users if u.email]
                
                if recipient_list:
                    # Prepare email context
                    context = {
                        'tenant': tenant,
                        'feature': selected_feature,
                        'platform_url': platform_url.rstrip('/'),
                        'support_email': support_email,
                    }
                    
                    html_message = render_to_string('emails/feature_message.html', context)
                    
                    try:
                        send_mail(
                            subject=f"HendAxis Update: {selected_feature.title}",
                            message=f"A new feature is available: {selected_feature.title}",
                            from_email=support_email,  # requested to be from support for non-sub messages
                            recipient_list=recipient_list,
                            html_message=html_message,
                            fail_silently=False,
                        )
                        
                        # Record that we sent this feature
                        FeatureIntroHistory.objects.create(
                            tenant=tenant,
                            feature_message=selected_feature
                        )
                        
                        emails_sent += len(recipient_list)
                        self.stdout.write(self.style.SUCCESS(f"Sent '{selected_feature.feature_key}' to {tenant.name} ({len(recipient_list)} recipients)"))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Failed to send email to {tenant.name}: {str(e)}"))
        
        self.stdout.write(self.style.SUCCESS(f"Finished processing feature messages. Total emails sent: {emails_sent}"))
