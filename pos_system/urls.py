"""
URL configuration for pos_system project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import Http404
import logging

logger = logging.getLogger(__name__)


def admin_honeypot(request):
    """Honeypot for /admin/ - logs attempts and returns 404."""
    logger.warning(
        f"Admin honeypot triggered: IP={request.META.get('REMOTE_ADDR')}, "
        f"User-Agent={request.META.get('HTTP_USER_AGENT', 'Unknown')}"
    )
    raise Http404("Not Found")


urlpatterns = [
    # Security: Changed from /admin/ to /super_office/
    path('super_office/', admin.site.urls),
    
    # Honeypot: Log attempts to access /admin/
    path('admin/', admin_honeypot, name='admin_honeypot'),
    path('admin/<path:subpath>', admin_honeypot),
    
    path('', include('apps.core.urls')),
    path('inventory/', include('apps.inventory.urls')),
    path('transfers/', include('apps.transfers.urls')),
    path('sales/', include('apps.sales.urls')),
    path('notifications/', include('apps.notifications.urls')),
    path('accounting/', include('apps.accounting.urls')),
    path('customers/', include('apps.customers.urls')),
    path('payments/', include('apps.payments.urls')),
    path('audit/', include('apps.audit.urls')),
    
    # Superadmin (Platform Management) - Superuser only
    path('superadmin/', include('apps.core.superadmin_urls')),
    
    # Email-based password reset (Admin users only - validated in custom view)
    path('password-reset/', include('django.contrib.auth.urls')),
]

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
