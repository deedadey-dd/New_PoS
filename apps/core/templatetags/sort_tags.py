from django import template
from django.utils.html import format_html
import urllib.parse

register = template.Library()

@register.simple_tag(takes_context=True)
def sort_link(context, display_name, field_name, extra_classes=''):
    """
    Generates a sortable table header link.
    Maintains existing query parameters (like pagination, search).
    """
    request = context['request']
    
    # Get current sorting params
    current_sort = request.GET.get('sort', '')
    current_dir = request.GET.get('dir', 'asc')

    # Determine next direction for this field
    if current_sort == field_name:
        next_dir = 'desc' if current_dir == 'asc' else 'asc'
        # Set icon
        icon = '<i class="bi bi-sort-down-alt ms-1 text-primary"></i>' if current_dir == 'asc' else '<i class="bi bi-sort-down ms-1 text-primary"></i>'
    else:
        next_dir = 'asc'
        icon = '<i class="bi bi-arrow-down-up ms-1 text-muted" style="opacity:0.3; font-size: 0.8em;"></i>'

    # Build new URL preserving other params
    query_dict = request.GET.copy()
    query_dict['sort'] = field_name
    query_dict['dir'] = next_dir
    
    # If starting a new sort, might want to reset pagination to page 1
    if 'page' in query_dict:
        del query_dict['page']
        
    url_params = query_dict.urlencode()
    url = f"?{url_params}"
    
    return format_html('<a href="{}" class="text-decoration-none text-dark d-flex align-items-center {}" style="white-space:nowrap;">{} {}</a>', url, extra_classes, display_name, format_html(icon))
