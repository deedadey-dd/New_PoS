"""
URL configuration for the payments app.
"""
from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    # Payment Provider Settings
    path('settings/', views.PaymentProviderSettingsView.as_view(), name='provider_settings'),
    path('settings/test-connection/', views.test_connection, name='test_connection'),
    
    # E-Cash Withdrawals
    path('withdrawals/', views.ECashWithdrawalListView.as_view(), name='withdrawal_list'),
    path('withdrawals/create/', views.ECashWithdrawalCreateView.as_view(), name='withdrawal_create'),
    path('withdrawals/<int:pk>/complete/', views.complete_withdrawal, name='withdrawal_complete'),
    path('withdrawals/<int:pk>/cancel/', views.cancel_withdrawal, name='withdrawal_cancel'),
    
    # Paystack Webhook (no login required)
    path('webhook/paystack/', views.paystack_webhook, name='paystack_webhook'),
    
    # E-Cash Ledger
    path('ledger/', views.ECashLedgerView.as_view(), name='ecash_ledger'),
]
