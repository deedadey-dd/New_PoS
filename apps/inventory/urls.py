"""
URL configuration for the inventory app.
"""
from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    # Categories
    path('categories/', views.CategoryListView.as_view(), name='category_list'),
    path('categories/create/', views.CategoryCreateView.as_view(), name='category_create'),
    path('categories/<int:pk>/edit/', views.CategoryUpdateView.as_view(), name='category_edit'),
    path('categories/<int:pk>/delete/', views.CategoryDeleteView.as_view(), name='category_delete'),
    
    # Products
    path('products/', views.ProductListView.as_view(), name='product_list'),
    path('products/create/', views.ProductCreateView.as_view(), name='product_create'),
    path('products/upload/', views.ProductBulkUploadView.as_view(), name='product_upload'),
    path('products/export/', views.InventoryExportView.as_view(), name='product_export'),
    path('products/template/', views.ProductTemplateDownloadView.as_view(), name='product_template'),
    path('products/<int:pk>/', views.ProductDetailView.as_view(), name='product_detail'),
    path('products/<int:pk>/edit/', views.ProductUpdateView.as_view(), name='product_edit'),
    path('products/<int:pk>/delete/', views.ProductDeleteView.as_view(), name='product_delete'),
    
    # Batches
    path('batches/', views.BatchListView.as_view(), name='batch_list'),
    path('batches/receive/', views.BatchCreateView.as_view(), name='batch_create'),
    path('batches/<int:pk>/', views.BatchDetailView.as_view(), name='batch_detail'),
    
    # Goods Receipts (Bulk Receiving)
    path('goods-receipts/', views.GoodsReceiptListView.as_view(), name='goods_receipt_list'),
    path('goods-receipts/create/', views.GoodsReceiptCreateView.as_view(), name='goods_receipt_create'),
    path('goods-receipts/<int:pk>/', views.GoodsReceiptDetailView.as_view(), name='goods_receipt_detail'),
    path('goods-receipts/<int:pk>/verify/', views.verify_goods_receipt, name='goods_receipt_verify'),
    
    # Stock
    path('stock/', views.StockOverviewView.as_view(), name='stock_overview'),
    path('stock/adjustment/', views.StockAdjustmentView.as_view(), name='stock_adjustment'),
    path('stock/adjustments/', views.AdjustmentHistoryView.as_view(), name='adjustment_history'),
    path('stock/adjustment/<int:pk>/review/', views.ReviewAdjustmentView.as_view(), name='review_adjustment'),
    path('ledger/', views.InventoryLedgerListView.as_view(), name='inventory_ledger'),
    
    # Shop Pricing (for Shop Managers)
    path('shop-prices/', views.ShopPriceListView.as_view(), name='shop_price_list'),
    path('shop-prices/<int:pk>/set/', views.ShopPriceSetView.as_view(), name='shop_price_set'),
    
    # API
    path('api/batches/', views.get_batches_for_product, name='api_batches'),
    path('api/products/search/', views.search_products, name='api_product_search'),
    path('api/products/autocomplete/', views.api_product_autocomplete, name='api_product_autocomplete'),
    path('api/products/<int:pk>/toggle-favorite/', views.ToggleFavoriteView.as_view(), name='api_toggle_favorite'),
    path('api/stock/adjustment/<int:pk>/details/', views.get_adjustment_details_api, name='api_adjustment_details'),
]
