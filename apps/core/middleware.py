"""
Middleware for the core app.
"""
from django.shortcuts import redirect
from django.urls import reverse


class TenantSetupMiddleware:
    """
    Middleware to redirect Admin users without a tenant to the tenant setup page.
    """
    EXEMPT_URLS = [
        '/setup/',
        '/accounts/logout/',
        '/static/',
        '/media/',
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Skip for unauthenticated users
        if not request.user.is_authenticated:
            return self.get_response(request)
        
        # Skip for superusers
        if request.user.is_superuser:
            return self.get_response(request)
        
        # Check if path is exempt
        path = request.path
        for exempt_url in self.EXEMPT_URLS:
            if path.startswith(exempt_url):
                return self.get_response(request)
        
        # Check if admin needs to set up tenant
        if hasattr(request.user, 'needs_tenant_setup') and request.user.needs_tenant_setup:
            setup_url = reverse('core:tenant_setup')
            if path != setup_url:
                return redirect(setup_url)
        
        return self.get_response(request)
