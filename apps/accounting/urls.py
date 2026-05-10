"""
URL configuration for the accounting app.
"""
from django.urls import path
from . import views

app_name = 'accounting'

urlpatterns = [
    path('dashboard/', views.AccountantDashboardView.as_view(), name='accountant_dashboard'),
    path('sales-report/', views.SalesReportView.as_view(), name='sales_report'),
    path('price-history/', views.PriceHistoryView.as_view(), name='price_history'),
    path('cash-transfers/', views.CashTransferListView.as_view(), name='cash_transfer_list'),
    path('cash-transfers/new/', views.CashTransferCreateView.as_view(), name='cash_transfer_create'),
    path('cash-transfers/<int:pk>/confirm/', views.CashTransferConfirmView.as_view(), name='cash_transfer_confirm'),
    path('cash-transfers/<int:pk>/cancel/', views.CashTransferCancelView.as_view(), name='cash_transfer_cancel'),
    path('cash-transfers/<int:pk>/receipt/', views.CashTransferReceiptView.as_view(), name='cash_transfer_receipt'),
    path('api/cash-transfers/<int:pk>/detail/', views.api_cash_transfer_detail, name='api_cash_transfer_detail'),

    # Excel Exports
    path('cash-transfers/export/', views.CashTransferExportView.as_view(), name='cash_transfer_export'),
    path('sales-report/export/', views.SalesReportExportView.as_view(), name='sales_report_export'),
    path('price-history/export/', views.PriceHistoryExportView.as_view(), name='price_history_export'),
    
    # Confirmations and Bank Transfers
    path('digital-confirmations/', views.DigitalPaymentConfirmationView.as_view(), name='digital_confirmations'),
    path('bank-transfers/new/', views.BankTransferCreateView.as_view(), name='bank_transfer_create'),
    path('bank-transfers/<int:pk>/receipt/', views.BankTransferReceiptView.as_view(), name='bank_transfer_receipt'),
    
    # Shop Local Momo Withdrawals
    path('shop-momo/', views.ShopMomoListView.as_view(), name='shop_momo_list'),
    path('shop-momo/<int:shop_id>/withdraw/', views.ShopMomoWithdrawView.as_view(), name='shop_momo_withdraw'),
    path('shop-momo/history/', views.ShopMomoHistoryView.as_view(), name='shop_momo_history'),
    path('shop-momo/history/export/', views.ShopMomoExportView.as_view(), name='shop_momo_export'),
]
