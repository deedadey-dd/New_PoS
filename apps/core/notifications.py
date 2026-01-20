"""
Notification services for sending alerts via Telegram.
SMS support can be added later.
"""
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def send_telegram_message(message: str, fail_silently: bool = True) -> bool:
    """
    Send a message via Telegram Bot API.
    
    Args:
        message: The message text to send
        fail_silently: If True, suppress exceptions
        
    Returns:
        bool: True if message was sent successfully
    """
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
    
    if not bot_token or not chat_id:
        logger.warning("Telegram not configured. TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing.")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }
        
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                logger.info("Telegram message sent successfully")
                return True
            else:
                logger.error(f"Telegram API error: {result}")
                return False
        else:
            logger.error(f"Telegram request failed: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        logger.error("Telegram request timed out")
        if not fail_silently:
            raise
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Telegram request error: {e}")
        if not fail_silently:
            raise
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending Telegram message: {e}")
        if not fail_silently:
            raise
        return False


def notify_new_contact(contact_message) -> bool:
    """
    Send notification for a new contact form submission.
    
    Args:
        contact_message: ContactMessage model instance
        
    Returns:
        bool: True if notification was sent successfully
    """
    # Format the message
    whatsapp_status = "✅ Yes" if contact_message.whatsapp_contact else "❌ No"
    
    message = f"""🔔 <b>New Contact Form Submission!</b>

👤 <b>Name:</b> {contact_message.name}
📱 <b>Phone:</b> {contact_message.phone}
📧 <b>Email:</b> {contact_message.email or 'Not provided'}
💬 <b>WhatsApp:</b> {whatsapp_status}

📝 <b>Message:</b>
{contact_message.message or 'No message provided'}

⏰ <i>Received at: {contact_message.created_at.strftime('%Y-%m-%d %H:%M:%S')}</i>
"""
    
    success = send_telegram_message(message)
    
    if success:
        contact_message.telegram_sent = True
        contact_message.save(update_fields=['telegram_sent'])
    
    return success
