from django import template

try:
    from user_agents import parse as parse_ua
except ImportError:
    parse_ua = None

register = template.Library()


@register.filter
def parse_device(value):
    """Convert a raw User-Agent string to 'PC - Windows - Chrome' format.

    If the value is already in friendly format (contains ' - ' and
    doesn't start with 'Mozilla'), return as-is.
    Uses user-agents library when available, falls back to string matching.
    """
    if not value or value == '-':
        return value

    # Already parsed
    if ' - ' in value and not value.startswith('Mozilla'):
        return value

    # ── Primary: user-agents library ──
    if parse_ua is not None:
        ua = parse_ua(value)

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

        if ua.device.brand and ua.device.brand != 'Other':
            model = ua.device.model if ua.device.model and ua.device.model != 'Other' else ''
            if model:
                result = f'{device_type} - {os_name} - {browser} ({ua.device.brand} {model})'
            else:
                result = f'{device_type} - {os_name} - {browser} ({ua.device.brand})'

        return result

    # ── Fallback: simple string matching ──
    ua_lower = value.lower()

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
