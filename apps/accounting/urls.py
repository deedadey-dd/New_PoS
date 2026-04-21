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
    path('cash-transfers/<int:pk>/print/', views.CashTransferPrintView.as_view(), name='cash_transfer_print'),

    # Excel Exports
    path('cash-transfers/export/', views.CashTransferExportView.as_view(), name='cash_transfer_export'),
    path('sales-report/export/', views.SalesReportExportView.as_view(), name='sales_report_export'),
    path('price-history/export/', views.PriceHistoryExportView.as_view(), name='price_history_export'),
    
    # Expenditures
    path('expenditures/', views.ExpenditureListView.as_view(), name='expenditure_list'),
    path('expenditures/new/', views.ExpenditureCreateView.as_view(), name='expenditure_create'),
    path('expenditures/<int:pk>/', views.ExpenditureDetailView.as_view(), name='expenditure_detail'),
    path('expenditures/item/<int:pk>/<str:action>/', views.ExpenditureItemActionView.as_view(), name='expenditure_item_action'),
    path('expenditures/categories/', views.ExpenditureCategoryView.as_view(), name='expenditure_categories'),
    path('expenditures/categories/api/add/', views.ApiAddExpenditureCategoryView.as_view(), name='api_add_category'),
    path('expenditures/categories/api/<int:pk>/delete/', views.ApiDeleteExpenditureCategoryView.as_view(), name='api_delete_category'),
    path('expenditures/report/', views.ExpenditureReportView.as_view(), name='expenditure_report'),
]
