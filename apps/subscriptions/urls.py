"""
URL configuration for the subscriptions app.
"""
from django.urls import path
from . import views

app_name = 'subscriptions'

urlpatterns = [
    # Tenant Admin subscription views
    path('', views.SubscriptionStatusView.as_view(), name='status'),
    path('history/', views.SubscriptionHistoryView.as_view(), name='history'),
    path('receipt/<int:pk>/download/', views.ReceiptDownloadView.as_view(), name='receipt_download'),
    path('receipt/<int:pk>/view/', views.ReceiptViewView.as_view(), name='receipt_view'),
    
    # Public pricing page
    path('pricing/', views.PricingPageView.as_view(), name='pricing'),
    
    # Tenant Manager views
    path('manager/', views.TenantManagerDashboardView.as_view(), name='tm_dashboard'),
    path('manager/tenant/<int:pk>/', views.TenantManagerTenantDetailView.as_view(), name='tm_tenant_detail'),
    path('manager/tenant/<int:pk>/record-payment/', views.TenantManagerRecordPaymentView.as_view(), name='tm_record_payment'),
    path('manager/tenant/<int:pk>/payments/', views.TenantManagerPaymentHistoryView.as_view(), name='tm_payment_history'),
]

