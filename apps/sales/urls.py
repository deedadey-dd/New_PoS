"""
URL configuration for the sales app.
"""
from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    # POS Interface
    path('pos/', views.POSView.as_view(), name='pos'),
    
    # Shop Settings
    path('shop-settings/', views.ShopSettingsUpdateView.as_view(), name='shop_settings'),
    path('shop-payments/<int:shop_id>/', views.AdminShopPaymentConfigView.as_view(), name='admin_shop_payment_settings'),
    
    # Shift Management
    path('shifts/', views.ShiftListView.as_view(), name='shift_list'),
    path('shift/open/', views.ShiftOpenView.as_view(), name='shift_open'),
    path('shift/<int:pk>/close/', views.ShiftCloseView.as_view(), name='shift_close'),
    path('shift/<int:pk>/receipt/', views.ShiftReceiptView.as_view(), name='shift_receipt'),
    path('api/shift/<int:pk>/detail/', views.api_shift_detail, name='api_shift_detail'),
    
    # Sales History
    path('', views.SaleListView.as_view(), name='sale_list'),
    path('<int:pk>/receipt/', views.SaleDetailView.as_view(), name='sale_receipt'),
    path('<int:pk>/waybill/', views.SaleWaybillView.as_view(), name='sale_waybill'),
    path('api/<int:pk>/detail/', views.api_sale_detail, name='api_sale_detail'),
    
    # API Endpoints
    path('api/products/search/', views.api_product_search, name='api_product_search'),
    path('api/checkout/', views.api_complete_sale, name='api_checkout'),
    path('api/<int:pk>/void/', views.api_void_sale, name='api_void_sale'),
    path('api/<int:pk>/refund/', views.api_refund_sale, name='api_refund_sale'),
    path('api/refund-requests/<int:pk>/approve/', views.api_approve_refund, name='api_approve_refund'),
    path('api/refund-requests/<int:pk>/reject/', views.api_reject_refund, name='api_reject_refund'),
    path('refund-requests/', views.RefundRequestListView.as_view(), name='refund_request_list'),
    path('api/invoices/<int:pk>/pay/', views.api_pay_invoice, name='api_pay_invoice'),
    path('api/sales/<int:pk>/dispatch/', views.api_dispatch_sale, name='api_dispatch_sale'),
    
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
