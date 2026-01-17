"""
Decorators for the core app.
"""
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.mixins import UserPassesTestMixin


def admin_required(view_func):
    """Decorator to require admin role."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('core:login')
        
        if not (request.user.is_superuser or (request.user.role and request.user.role.name == 'ADMIN')):
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('core:dashboard')
        
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def role_required(*roles):
    """Decorator to require specific roles."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('core:login')
            
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            
            if request.user.role and request.user.role.name in roles:
                return view_func(request, *args, **kwargs)
            
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('core:dashboard')
        return _wrapped_view
    return decorator


class RoleRequiredMixin(UserPassesTestMixin):
    """
    Mixin for class-based views that restricts access to specific roles.
    Usage: Set `allowed_roles` attribute on the view class.
    """
    allowed_roles = []
    permission_denied_message = 'You do not have permission to access this page.'
    
    def test_func(self):
        user = self.request.user
        if user.is_superuser:
            return True
        if user.role and user.role.name in self.allowed_roles:
            return True
        return False
    
    def handle_no_permission(self):
        messages.error(self.request, self.permission_denied_message)
        return redirect('core:dashboard')


class AdminOrManagerRequiredMixin(RoleRequiredMixin):
    """
    Mixin that restricts access to Admin and Manager roles only.
    Shop Attendants are explicitly excluded.
    """
    allowed_roles = ['ADMIN', 'PRODUCTION_MANAGER', 'STORES_MANAGER', 'SHOP_MANAGER']


class AdminRequiredMixin(RoleRequiredMixin):
    """
    Mixin that restricts access to ADMIN role only.
    """
    allowed_roles = ['ADMIN']
    permission_denied_message = 'Only administrators can access this page.'
