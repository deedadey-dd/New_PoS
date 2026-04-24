"""
URL configuration for the sales app.
"""
from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    # POS Interface
    path('pos/', views.POSView.as_view(), name='pos'),
    path('cashier/', views.CashierPendingInvoicesView.as_view(), name='cashier_invoices'),
    path('dispatch/', views.ManagerDispatchView.as_view(), name='dispatch_invoices'),
    
    # Shop Settings
    path('shop-settings/', views.ShopSettingsUpdateView.as_view(), name='shop_settings'),
    path('shop-payments/<int:shop_id>/', views.AdminShopPaymentConfigView.as_view(), name='admin_shop_payment_settings'),
    
    # Shift Management
    path('shift/open/', views.ShiftOpenView.as_view(), name='shift_open'),
    path('shift/<int:pk>/close/', views.ShiftCloseView.as_view(), name='shift_close'),
    
    # Sales History
    path('', views.SaleListView.as_view(), name='sale_list'),
    path('<int:pk>/receipt/', views.SaleDetailView.as_view(), name='sale_receipt'),
    path('api/<int:pk>/detail/', views.api_sale_detail, name='api_sale_detail'),
    
    # API Endpoints
    path('api/products/search/', views.api_product_search, name='api_product_search'),
    path('api/customer-search/', views.api_customer_search, name='api_customer_search'),
    path('api/checkout/', views.api_complete_sale, name='api_checkout'),
    path('api/pay-invoice/', views.api_pay_invoice, name='api_pay_invoice'),
    path('api/dispatch-invoice/', views.api_dispatch_invoice, name='api_dispatch_invoice'),
    path('api/invoices/delete/', views.api_delete_invoices, name='api_delete_invoices'),
    path('api/invoices/<int:pk>/update/', views.api_update_invoice, name='api_update_invoice'),
    path('api/<int:pk>/void/', views.api_void_sale, name='api_void_sale'),
    path('api/<int:pk>/revert-payment/', views.api_revert_payment, name='api_revert_payment'),
    path('api/<int:pk>/revert-dispatch/', views.api_revert_dispatch, name='api_revert_dispatch'),
    
    # Invoice Management (Cashier)
    path('invoices/<int:pk>/edit/', views.InvoiceEditView.as_view(), name='invoice_edit'),
    path('invoices/<int:pk>/print/', views.invoice_print_view, name='invoice_print'),
    
    # E-Cash Payment
    path('api/ecash/initialize/', views.initialize_ecash_payment, name='initialize_ecash_payment'),
    path('api/ecash/verify/', views.verify_ecash_payment, name='verify_ecash_payment'),
    
    # Offline Sync
    path('api/sync-offline/', views.api_sync_offline_sales, name='api_sync_offline_sales'),
    
    # Reports
    path('report/', views.ShopSalesReportView.as_view(), name='shop_sales_report'),

    # Excel Exports
    path('export/', views.SaleListExportView.as_view(), name='sale_list_export'),
    path('report/export/', views.ShopSalesReportExportView.as_view(), name='shop_sales_report_export'),
]
