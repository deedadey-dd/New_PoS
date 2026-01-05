"""
Custom template filters for transfers app.
"""
from django import template

register = template.Library()


@register.filter(name='lookup')
def lookup(form, key):
    """
    Look up a form field by dynamic name.
    Usage: {{ form|lookup:item.pk }}
    This will look for a field named 'received_{key}' in the form.
    """
    field_name = f'received_{key}'
    if hasattr(form, 'fields') and field_name in form.fields:
        return form[field_name]
    return ''


@register.filter(name='get_item')
def get_item(dictionary, key):
    """
    Look up a value in a dictionary by key.
    Usage: {{ mydict|get_item:mykey }}
    Returns None if key not found or dictionary is not a dict.
    """
    if dictionary is None:
        return None
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None
