"""
Custom template filters for inventory app.
"""
from django import template

register = template.Library()


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
