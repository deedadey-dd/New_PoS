import logging
import random
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.conf import settings
from django.template.loader import render_to_string
from django.core.mail import send_mail

from apps.core.models import Tenant, User, ScheduledEmail, FeatureIntroHistory

logger = logging.getLogger(__name__)

FEATURE_INTROS = [
    {
        'key': 'advanced_reporting',
        'title': 'Unlock Advanced Reporting',
        'content': 'Did you know you can track your top-selling products and sales trends in real-time? Head over to the Reports tab to discover more insights about your business.',
    },
    {
        'key': 'inventory_alerts',
        'title': 'Never Run Out of Stock',
        'content': 'Set reorder levels for your critical items. HendAxis PoS will automatically alert your shop managers when stock falls below the threshold!',
    },
    {
        'key': 'digital_payments',
        'title': 'Streamline Digital Payments',
        'content': 'Accept Mobile Money and card payments seamlessly. Our integrated payment flows reduce reconciliation errors and save your cashiers time.',
    },
    {
        'key': 'user_roles',
        'title': 'Secure Your Business with User Roles',
        'content': 'Ensure your staff only access what they need. Assign specific roles like Shop Attendant, Manager, or Accountant to control permissions effectively.',
    }
]

class Command(BaseCommand):
    help = 'Process custom scheduled emails and monthly feature introductions.'

    def handle(self, *args, **options):
        now = timezone.now()
        
        self.stdout.write(f"Processing scheduled emails at: {now}")
        
        emails_sent = 0
        
        # 1. Process Custom Scheduled Emails
        pending_emails = ScheduledEmail.objects.filter(status='PENDING', scheduled_time__lte=now)
        
        for scheduled_email in pending_emails:
            try:
                # Determine target tenants
                if scheduled_email.tenant:
                    # Sent by a Tenant Admin to their own users
                    tenants = [scheduled_email.tenant]
                else:
                    # Sent by Superadmin
                    if scheduled_email.target_tenants.exists():
                        tenants = scheduled_email.target_tenants.all()
                    else:
                        tenants = Tenant.objects.filter(is_active=True)
                
                # Gather recipients
                recipient_emails = set()
                for t in tenants:
                    if scheduled_email.target_audience == 'ADMINS':
                        users = User.objects.filter(tenant=t, is_active=True, role__name='ADMIN')
                    else: # ALL_USERS
                        users = User.objects.filter(tenant=t, is_active=True)
                        
                    for u in users:
                        if u.email:
                            recipient_emails.add(u.email)
                
                if recipient_emails:
                    context = {
                        'subject': scheduled_email.subject,
                        'body': scheduled_email.body,
                        'support_email': settings.SUPPORT_EMAIL,
                    }
                    html_message = render_to_string('emails/custom_message.html', context)
                    
                    send_mail(
                        subject=scheduled_email.subject,
                        message="Please view this email in an HTML compatible client.",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=list(recipient_emails),
                        html_message=html_message,
                        fail_silently=False,
                    )
                    emails_sent += len(recipient_emails)
                    
                scheduled_email.status = 'SENT'
                scheduled_email.sent_at = now
                scheduled_email.save()
                self.stdout.write(self.style.SUCCESS(f"Sent custom email ID {scheduled_email.id} to {len(recipient_emails)} recipients."))
                
            except Exception as e:
                scheduled_email.status = 'FAILED'
                scheduled_email.error_log = str(e)
                scheduled_email.save()
                self.stdout.write(self.style.ERROR(f"Failed to send email ID {scheduled_email.id}: {str(e)}"))
                
        # 2. Process Monthly Feature Introductions
        # Check if today is the 1st Tuesday of the month
        if self.is_first_tuesday(now.date()):
            self.stdout.write("Today is the 1st Tuesday. Processing feature introductions...")
            active_tenants = Tenant.objects.filter(is_active=True)
            
            for tenant in active_tenants:
                # Find a feature they haven't received
                sent_history = FeatureIntroHistory.objects.filter(tenant=tenant).values_list('feature_key', flat=True)
                available_features = [f for f in FEATURE_INTROS if f['key'] not in sent_history]
                
                if not available_features:
                    # They received all of them, reset history for this tenant to cycle again
                    FeatureIntroHistory.objects.filter(tenant=tenant).delete()
                    available_features = FEATURE_INTROS
                    
                selected_feature = random.choice(available_features)
                
                # Send to Tenant Admins
                admins = User.objects.filter(tenant=tenant, is_active=True, role__name='ADMIN')
                recipients = [u.email for u in admins if u.email]
                
                if recipients:
                    feedback_link = f"{getattr(settings, 'PLATFORM_URL', 'http://127.0.0.1:8000')}/contact/?topic=feature_feedback&feature={selected_feature['key']}"
                    
                    # We can use the custom_message template
                    body_html = f"<h3>{selected_feature['title']}</h3><p>{selected_feature['content']}</p><br><p><a href='{feedback_link}' style='display:inline-block;padding:10px 15px;background:#0d6efd;color:white;text-decoration:none;border-radius:4px;'>Give us Feedback</a></p>"
                    
                    context = {
                        'subject': f"Feature Spotlight: {selected_feature['title']}",
                        'body': body_html,
                        'support_email': settings.SUPPORT_EMAIL,
                    }
                    html_message = render_to_string('emails/custom_message.html', context)
                    
                    try:
                        send_mail(
                            subject=context['subject'],
                            message=selected_feature['content'],
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=recipients,
                            html_message=html_message,
                            fail_silently=False,
                        )
                        # Record history
                        FeatureIntroHistory.objects.create(tenant=tenant, feature_key=selected_feature['key'])
                        emails_sent += len(recipients)
                        self.stdout.write(self.style.SUCCESS(f"Sent feature '{selected_feature['key']}' to {tenant.name}"))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Failed to send feature intro to {tenant.name}: {str(e)}"))
                        
        self.stdout.write(self.style.SUCCESS(f"Finished processing. Total emails sent: {emails_sent}"))

    def is_first_tuesday(self, date_obj):
        # Weekday 1 is Tuesday (Monday=0, Tuesday=1)
        # It's the first Tuesday if weekday is 1 and day of month is between 1 and 7
        return date_obj.weekday() == 1 and 1 <= date_obj.day <= 7
