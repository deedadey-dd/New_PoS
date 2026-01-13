"""
URL configuration for pos_system project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.core.urls')),
    path('inventory/', include('apps.inventory.urls')),
    path('transfers/', include('apps.transfers.urls')),
    path('sales/', include('apps.sales.urls')),
    path('notifications/', include('apps.notifications.urls')),
    path('accounting/', include('apps.accounting.urls')),
    path('customers/', include('apps.customers.urls')),
    path('payments/', include('apps.payments.urls')),
    
    # Email-based password reset (Admin users only - validated in custom view)
    path('password-reset/', include('django.contrib.auth.urls')),
]

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
