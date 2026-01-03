"""
Context processors for the core app.
"""


def tenant_context(request):
    """
    Add tenant information to template context.
    """
    context = {
        'current_tenant': None,
        'currency_symbol': '$',
    }
    
    if request.user.is_authenticated and hasattr(request.user, 'tenant') and request.user.tenant:
        context['current_tenant'] = request.user.tenant
        context['currency_symbol'] = request.user.tenant.currency_symbol
    
    return context
