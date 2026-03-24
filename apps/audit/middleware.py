import json
from django.utils.deprecation import MiddlewareMixin
from django.urls import resolve
from .models import UserActivity

try:
    from user_agents import parse as parse_ua
except ImportError:
    parse_ua = None


def _friendly_device_info(ua_string):
    """Parse a User-Agent string into 'PC - Windows - Chrome' format.
    
    Uses the user-agents library when available, falls back to regex.
    """
    if not ua_string:
        return ''

    # ── Primary: user-agents library ──
    if parse_ua is not None:
        ua = parse_ua(ua_string)

        if ua.is_mobile:
            device_type = 'Mobile'
        elif ua.is_tablet:
            device_type = 'Tablet'
        elif ua.is_pc:
            device_type = 'PC'
        elif ua.is_bot:
            device_type = 'Bot'
        else:
            device_type = 'Other'

        os_name = ua.os.family or 'Unknown OS'
        browser = ua.browser.family or 'Unknown Browser'
        result = f'{device_type} - {os_name} - {browser}'

        # Append device brand/model for mobile/tablet
        if ua.device.brand and ua.device.brand != 'Other':
            model = ua.device.model if ua.device.model and ua.device.model != 'Other' else ''
            if model:
                result = f'{device_type} - {os_name} - {browser} ({ua.device.brand} {model})'
            else:
                result = f'{device_type} - {os_name} - {browser} ({ua.device.brand})'

        return result[:255]

    # ── Fallback: simple string matching ──
    ua_lower = ua_string.lower()

    if any(k in ua_lower for k in ('iphone', 'ipod', 'android mobile', 'mobile')):
        device_type = 'Tablet' if ('ipad' in ua_lower or 'tablet' in ua_lower) else 'Mobile'
    elif 'ipad' in ua_lower or 'tablet' in ua_lower:
        device_type = 'Tablet'
    elif any(k in ua_lower for k in ('bot', 'crawl', 'spider')):
        device_type = 'Bot'
    else:
        device_type = 'PC'

    if 'windows' in ua_lower:      os_name = 'Windows'
    elif 'iphone' in ua_lower or 'ipad' in ua_lower: os_name = 'iOS'
    elif 'mac os' in ua_lower or 'macintosh' in ua_lower: os_name = 'macOS'
    elif 'android' in ua_lower:     os_name = 'Android'
    elif 'cros' in ua_lower:        os_name = 'ChromeOS'
    elif 'linux' in ua_lower:       os_name = 'Linux'
    else:                           os_name = 'Unknown OS'

    if 'edg/' in ua_lower:          browser = 'Edge'
    elif 'opr/' in ua_lower or 'opera' in ua_lower: browser = 'Opera'
    elif 'firefox' in ua_lower:     browser = 'Firefox'
    elif 'chrome' in ua_lower and 'chromium' not in ua_lower: browser = 'Chrome'
    elif 'safari' in ua_lower and 'chrome' not in ua_lower: browser = 'Safari'
    elif 'msie' in ua_lower or 'trident' in ua_lower: browser = 'Internet Explorer'
    else:                           browser = 'Unknown Browser'

    return f'{device_type} - {os_name} - {browser}'

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
