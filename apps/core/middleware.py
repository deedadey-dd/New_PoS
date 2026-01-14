"""
Middleware for the core app.
"""
from django.shortcuts import redirect, render
from django.urls import reverse


class TenantSetupMiddleware:
    """
    Middleware to redirect Admin users without a tenant to the tenant setup page.
    Also handles forced password change after admin reset.
    Also checks tenant subscription status.
    """
    EXEMPT_URLS = [
        '/setup/',
        '/accounts/logout/',
        '/logout/',
        '/change-password/',  # Forced password change page
        '/static/',
        '/media/',
        '/super_office/',  # Updated from /admin/
        '/superadmin/',
        '/subscription-expired/',
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
        
        # Check if user needs to change password
        if hasattr(request.user, 'password_reset_required') and request.user.password_reset_required:
            change_password_url = reverse('core:forced_password_change')
            if path != change_password_url:
                return redirect(change_password_url)
        
        # Check if admin needs to set up tenant
        if hasattr(request.user, 'needs_tenant_setup') and request.user.needs_tenant_setup:
            setup_url = reverse('core:tenant_setup')
            if path != setup_url:
                return redirect(setup_url)
        
        # Check tenant subscription status
        if hasattr(request.user, 'tenant') and request.user.tenant:
            tenant = request.user.tenant
            if not tenant.is_active or tenant.subscription_status in ['EXPIRED', 'SUSPENDED']:
                # Show subscription expired page
                return render(request, 'core/subscription_expired.html', {
                    'tenant': tenant,
                    'status': tenant.subscription_status,
                })
        
        return self.get_response(request)
