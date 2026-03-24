import json
from django.utils.deprecation import MiddlewareMixin
from django.urls import resolve
from .models import UserActivity

try:
    from user_agents import parse as parse_ua
except ImportError:
    parse_ua = None


def _friendly_device_info(ua_string):
    """Parse a User-Agent string into a human-readable summary."""
    if not ua_string:
        return ''
    if parse_ua is None:
        return ua_string[:255]

    ua = parse_ua(ua_string)
    browser = ua.browser.family
    if ua.browser.version_string:
        browser += f' {ua.browser.version_string}'

    os_name = ua.os.family
    if ua.os.version_string:
        os_name += f' {ua.os.version_string}'

    if ua.is_mobile:
        device_type = 'Mobile'
    elif ua.is_tablet:
        device_type = 'Tablet'
    elif ua.is_pc:
        device_type = 'Desktop'
    elif ua.is_bot:
        device_type = 'Bot'
    else:
        device_type = 'Unknown'

    device_brand = ''
    if ua.device.brand and ua.device.brand != 'Other':
        device_brand = ua.device.brand
        if ua.device.model and ua.device.model != 'Other':
            device_brand += f' {ua.device.model}'

    parts = [browser, os_name, device_type]
    if device_brand:
        parts.append(device_brand)
    return ' / '.join(parts)[:255]

class ActivityLoggingMiddleware(MiddlewareMixin):
    """
    Middleware to log user activity (POST/PUT/DELETE requests).
    Login/Logout will be handled by standard Django signals or this middleware if auth views are hit.
    """
    
    def process_request(self, request):
        request.old_post_data = request.POST.copy()

    def process_response(self, request, response):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return response
            
        # We only care about tenant users
        if not getattr(request.user, 'tenant', None):
            return response

        # Determine action based on HTTP method and path
        method = request.method
        path = request.path
        action = None
        details = ""

        # Check for login/logout
        if method == 'POST':
            if 'login' in path:
                action = 'LOGIN'
            elif 'logout' in path:
                action = 'LOGOUT'
            else:
                action = 'CREATE' if 'create' in path or 'add' in path else 'UPDATE'
                
                # Try to mask sensitive data
                post_data = request.old_post_data if hasattr(request, 'old_post_data') else request.POST.copy()
                for key in ['password', 'csrfmiddlewaretoken', 'signature']:
                    if key in post_data:
                        post_data[key] = '***'
                try:
                    details = json.dumps(post_data.dict())
                except Exception:
                    details = "Data could not be serialized"
                    
        elif method in ['PUT', 'PATCH']:
            action = 'UPDATE'
            details = "Updated resource via API or form"
        elif method == 'DELETE':
            action = 'DELETE'
            details = "Deleted resource"
            
        # Log if we identified an action
        if action:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip = x_forwarded_for.split(',')[0]
            else:
                ip = request.META.get('REMOTE_ADDR')
                
            device_info = _friendly_device_info(request.META.get('HTTP_USER_AGENT', ''))
                
            UserActivity.objects.create(
                tenant=request.user.tenant,
                user=request.user,
                action=action,
                path=path,
                ip_address=ip,
                device_info=device_info,
                details=details
            )

        return response
