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
    path('tenants/<int:pk>/unlock/', superadmin_views.TenantUnlockView.as_view(), name='tenant_unlock'),
    path('tenants/<int:pk>/payments/', superadmin_views.TenantPaymentListView.as_view(), name='tenant_payments'),
    path('tenants/<int:pk>/payments/record/', superadmin_views.RecordPaymentView.as_view(), name='record_payment'),
    path('payments/', superadmin_views.AllPaymentsView.as_view(), name='all_payments'),
    
    # Tenant Managers
    path('managers/', superadmin_views.TenantManagerListView.as_view(), name='tenant_manager_list'),
    path('managers/create/', superadmin_views.TenantManagerCreateView.as_view(), name='tenant_manager_create'),
    path('managers/<int:pk>/', superadmin_views.TenantManagerDetailView.as_view(), name='tenant_manager_detail'),
    path('managers/<int:pk>/edit/', superadmin_views.TenantManagerEditView.as_view(), name='tenant_manager_edit'),
    path('managers/<int:pk>/delete/', superadmin_views.TenantManagerDeleteView.as_view(), name='tenant_manager_delete'),
    
    # Contact Messages
    path('contacts/', superadmin_views.ContactMessageListView.as_view(), name='contact_list'),
    path('contacts/<int:pk>/', superadmin_views.ContactMessageDetailView.as_view(), name='contact_detail'),
    path('contacts/<int:pk>/notes/', superadmin_views.ContactMessageUpdateNotesView.as_view(), name='contact_notes'),
    path('contacts/<int:pk>/mark-read/', superadmin_views.ContactMessageMarkReadView.as_view(), name='contact_mark_read'),
    path('contacts/<int:pk>/delete/', superadmin_views.ContactMessageDeleteView.as_view(), name='contact_delete'),
    
    # Emails
    path('emails/', superadmin_views.EmailListView.as_view(), name='email_list'),
    path('emails/compose/', superadmin_views.EmailComposeView.as_view(), name='email_compose'),
    
    # Feature Messages
    path('features/', superadmin_views.FeatureMessageListView.as_view(), name='feature_message_list'),
    path('features/create/', superadmin_views.FeatureMessageCreateView.as_view(), name='feature_message_create'),
    path('features/<int:pk>/', superadmin_views.FeatureMessageDetailView.as_view(), name='feature_message_detail'),
    path('features/<int:pk>/send/', superadmin_views.FeatureMessageSendView.as_view(), name='feature_message_send'),
    path('features/<int:pk>/edit/', superadmin_views.FeatureMessageUpdateView.as_view(), name='feature_message_edit'),
    path('features/<int:pk>/toggle/', superadmin_views.FeatureMessageToggleView.as_view(), name='feature_message_toggle'),
    path('features/<int:pk>/delete/', superadmin_views.FeatureMessageDeleteView.as_view(), name='feature_message_delete'),
    path('features/upload-image/', superadmin_views.FeatureImageUploadView.as_view(), name='feature_image_upload'),
]
