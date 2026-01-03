"""
Decorators for the core app.
"""
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


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
