import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.urls import reverse
from datetime import timedelta

from apps.core.models import Tenant, User

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Process daily subscription reminders for 1 month, 1 week, and 1 day expirations.'

    def handle(self, *args, **options):
        today = timezone.now().date()
        
        # Calculate target dates
        one_month = today + timedelta(days=30)
        one_week = today + timedelta(days=7)
        one_day = today + timedelta(days=1)
        
        self.stdout.write(f"Processing subscription reminders for today: {today}")
        
        tenants = Tenant.objects.filter(is_active=True, subscription_end_date__isnull=False)
        emails_sent = 0
        
        for tenant in tenants:
            exp_date = tenant.subscription_end_date.date()
            reminder_type = None
            
            if exp_date == one_month:
                reminder_type = "1 Month"
            elif exp_date == one_week:
                reminder_type = "1 Week"
            elif exp_date == one_day:
                reminder_type = "1 Day"
                
            if reminder_type:
                # Find the target audience
                # By default, send to Tenant Admins
                target_users = User.objects.filter(tenant=tenant, is_active=True, role__name='ADMIN')
                
                if target_users.exists():
                    recipient_list = [u.email for u in target_users if u.email]
                    
                    if recipient_list:
                        # Prepare email content
                        context = {
                            'tenant': tenant,
                            'plan_name': tenant.subscription_plan.name if tenant.subscription_plan else 'Custom',
                            'expiry_date': tenant.subscription_end_date,
                            'platform_url': getattr(settings, 'PLATFORM_URL', 'http://127.0.0.1:8000'),
                            'support_email': settings.SUPPORT_EMAIL,
                        }
                        
                        html_message = render_to_string('emails/subscription_reminder.html', context)
                        subject = f"Action Required: Your HendAxis PoS Subscription Expires in {reminder_type}"
                        
                        try:
                            send_mail(
                                subject=subject,
                                message=f"Your subscription expires on {exp_date}. Please login to renew.",
                                from_email=getattr(settings, 'SALES_EMAIL', 'sales@hendaxis.com'),
                                recipient_list=recipient_list,
                                html_message=html_message,
                                fail_silently=False,
                            )
                            emails_sent += len(recipient_list)
                            self.stdout.write(self.style.SUCCESS(f"Sent {reminder_type} reminder to {tenant.name} ({len(recipient_list)} recipients)"))
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Failed to send email to {tenant.name}: {str(e)}"))
        
        self.stdout.write(self.style.SUCCESS(f"Finished processing. Total emails sent: {emails_sent}"))
