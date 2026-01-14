"""
URL patterns for superadmin (platform management) views.
"""
from django.urls import path
from . import superadmin_views

app_name = 'superadmin'

urlpatterns = [
    path('', superadmin_views.SuperadminDashboardView.as_view(), name='dashboard'),
    path('tenants/', superadmin_views.TenantListView.as_view(), name='tenant_list'),
    path('tenants/<int:pk>/', superadmin_views.TenantDetailView.as_view(), name='tenant_detail'),
    path('tenants/<int:pk>/activate/', superadmin_views.TenantActivateView.as_view(), name='tenant_activate'),
    path('tenants/<int:pk>/deactivate/', superadmin_views.TenantDeactivateView.as_view(), name='tenant_deactivate'),
    path('tenants/<int:pk>/subscription/', superadmin_views.TenantSubscriptionView.as_view(), name='tenant_subscription'),
    path('tenants/<int:pk>/extend/', superadmin_views.TenantExtendView.as_view(), name='tenant_extend'),
]
