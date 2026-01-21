"""
Notification services for subscription alerts.
Supports email (when configured) and mNotify SMS as fallback.
"""
import requests
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Handles sending subscription notifications via email or SMS.
    Falls back to SMS when email is not configured.
    """
    
    @classmethod
    def send_subscription_notification(cls, tenant, notification_type, days_info=None):
        """
        Send subscription notification to tenant admin(s).
        
        Args:
            tenant: Tenant instance
            notification_type: 'expiry_warning', 'expired', 'deactivated', 'locked'
            days_info: Number of days (positive = until expiry, negative = since expiry)
        
        Returns:
            tuple: (success: bool, channel: str, error_message: str or None)
        """
        # Get tenant admin(s)
        from apps.core.models import User
        admins = User.objects.filter(
            tenant=tenant,
            role__name='ADMIN',
            is_active=True
        )
        
        if not admins.exists():
            return False, None, "No active admin found for tenant"
        
        # Build notification content
        context = cls._build_notification_context(tenant, notification_type, days_info)
        
        # Try email first if enabled
        if settings.EMAIL_NOTIFICATIONS_ENABLED:
            success, error = cls._send_email_notification(admins, context)
            if success:
                return True, 'EMAIL', None
            logger.warning(f"Email notification failed for {tenant.name}: {error}")
        
        # Fallback to SMS if enabled
        if settings.SMS_NOTIFICATIONS_ENABLED and settings.MNOTIFY_API_KEY:
            success, error = cls._send_sms_notification(admins, context)
            if success:
                return True, 'SMS', None
            logger.warning(f"SMS notification failed for {tenant.name}: {error}")
            return False, 'SMS', error
        
        return False, None, "No notification channel configured"
    
    @classmethod
    def _build_notification_context(cls, tenant, notification_type, days_info):
        """Build context for notification templates."""
        messages = {
            'expiry_warning': {
                'subject': f'Subscription Expiring Soon - {tenant.name}',
                'title': 'Subscription Expiring Soon',
                'urgency': 'warning',
            },
            'expired': {
                'subject': f'Subscription Expired - {tenant.name}',
                'title': 'Subscription Has Expired',
                'urgency': 'danger',
            },
            'deactivated': {
                'subject': f'Account Deactivated - {tenant.name}',
                'title': 'Account Has Been Deactivated',
                'urgency': 'danger',
            },
            'locked': {
                'subject': f'Account Locked - {tenant.name}',
                'title': 'Account Has Been Locked',
                'urgency': 'critical',
            },
        }
        
        config = messages.get(notification_type, messages['expiry_warning'])
        
        # Build SMS message (short version)
        if notification_type == 'expiry_warning':
            sms_message = f"POS Alert: Your subscription expires in {days_info} days. Please renew to avoid service interruption."
        elif notification_type == 'expired':
            sms_message = f"POS Alert: Your subscription expired {abs(days_info)} days ago. Renew now to restore full access."
        elif notification_type == 'deactivated':
            sms_message = "POS Alert: Your account has been deactivated due to expired subscription. Contact support to reactivate."
        else:
            sms_message = "POS Alert: Your account has been locked. Please contact support."
        
        return {
            'tenant': tenant,
            'notification_type': notification_type,
            'days_info': days_info,
            'sms_message': sms_message,
            **config,
        }
    
    @classmethod
    def _send_email_notification(cls, admins, context):
        """Send email notification to admins."""
        try:
            # Render email templates
            html_message = render_to_string(
                'notifications/subscription_expiry_email.html',
                context
            )
            plain_message = strip_tags(html_message)
            
            recipient_list = [admin.email for admin in admins if admin.email]
            
            if not recipient_list:
                return False, "No valid email addresses"
            
            send_mail(
                subject=context['subject'],
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=recipient_list,
                html_message=html_message,
                fail_silently=False,
            )
            return True, None
        except Exception as e:
            logger.error(f"Email send error: {str(e)}")
            return False, str(e)
    
    @classmethod
    def _send_sms_notification(cls, admins, context):
        """Send SMS notification via mNotify."""
        try:
            # Get phone numbers
            phone_numbers = [
                cls._format_phone_number(admin.phone)
                for admin in admins
                if admin.phone
            ]
            
            if not phone_numbers:
                return False, "No valid phone numbers"
            
            # mNotify API endpoint
            url = "https://apps.mnotify.net/smsapi"
            
            for phone in phone_numbers:
                params = {
                    'key': settings.MNOTIFY_API_KEY,
                    'to': phone,
                    'msg': context['sms_message'],
                    'sender_id': settings.MNOTIFY_SENDER_ID,
                }
                
                response = requests.get(url, params=params, timeout=30)
                
                # mNotify returns code=1000 for success
                if response.status_code != 200:
                    logger.warning(f"mNotify API error: {response.status_code}")
                else:
                    try:
                        result = response.json()
                        if result.get('code') != '1000':
                            logger.warning(f"mNotify send error: {result}")
                    except:
                        pass
            
            return True, None
        except Exception as e:
            logger.error(f"SMS send error: {str(e)}")
            return False, str(e)
    
    @classmethod
    def _format_phone_number(cls, phone):
        """Format phone number for mNotify (Ghana format)."""
        if not phone:
            return None
        
        # Remove spaces, hyphens, and other characters
        phone = ''.join(filter(str.isdigit, phone))
        
        # Ghana number formatting
        if phone.startswith('0'):
            phone = '233' + phone[1:]
        elif not phone.startswith('233'):
            phone = '233' + phone
        
        return phone
