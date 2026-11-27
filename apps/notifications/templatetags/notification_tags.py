from django import template
from apps.notifications.navigation import resolve_target_url, resolve_action_label

register = template.Library()

@register.filter(name='target_url')
def target_url(item, user):
    """
    Template filter to get role-specific target URL for notification or bulletin.
    Usage: {{ notification|target_url:request.user }}
    """
    if not item:
        return ''
    if hasattr(item, 'get_target_url'):
        return item.get_target_url(user)
    return resolve_target_url(item, user) or ''


@register.filter(name='action_label')
def action_label(item, user):
    """
    Template filter to get role-specific action button label.
    Usage: {{ post|action_label:request.user }}
    """
    if not item:
        return 'View Details'
    if hasattr(item, 'get_action_label'):
        return item.get_action_label(user)
    return resolve_action_label(item, user)
