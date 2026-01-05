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
]
