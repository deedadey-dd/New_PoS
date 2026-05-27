"""
URL configuration for the payments app.
"""
from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    # Payment Provider Configs
    path('settings/', views.PaymentProviderConfigListView.as_view(), name='provider_settings'),
    path('settings/create/', views.PaymentProviderConfigCreateView.as_view(), name='config_create'),
    path('settings/<int:pk>/update/', views.PaymentProviderConfigUpdateView.as_view(), name='config_update'),
    path('settings/test-connection/', views.test_connection, name='test_connection'),
    
    # Shop Payment Assignments
    path('settings/assignments/', views.ShopPaymentAssignmentListView.as_view(), name='shop_assignments'),
    path('settings/assignments/create/', views.ShopPaymentAssignmentCreateView.as_view(), name='assignment_create'),
    path('settings/assignments/<int:pk>/update/', views.ShopPaymentAssignmentUpdateView.as_view(), name='assignment_update'),
    
    # E-Cash Withdrawals
    path('withdrawals/', views.ECashWithdrawalListView.as_view(), name='withdrawal_list'),
    path('withdrawals/create/', views.ECashWithdrawalCreateView.as_view(), name='withdrawal_create'),
    path('withdrawals/<int:pk>/complete/', views.complete_withdrawal, name='withdrawal_complete'),
    path('withdrawals/<int:pk>/cancel/', views.cancel_withdrawal, name='withdrawal_cancel'),
    
    # Paystack Webhook (no login required)
    path('webhook/paystack/', views.paystack_webhook, name='paystack_webhook'),
    
    # E-Cash Ledger
    path('ledger/', views.ECashLedgerView.as_view(), name='ecash_ledger'),
    path('ledger/export/', views.ECashLedgerExportView.as_view(), name='ecash_ledger_export'),
    
    # Shop E-Cash Management (new)
    path('shops/', views.ShopECashListView.as_view(), name='shop_ecash_list'),
    path('shops/<int:shop_id>/withdraw/', views.ShopECashWithdrawView.as_view(), name='shop_ecash_withdraw'),
    path('shops/history/', views.ShopECashHistoryView.as_view(), name='shop_ecash_history'),
    path('shops/history/export/', views.ShopECashExportView.as_view(), name='shop_ecash_export'),
]
