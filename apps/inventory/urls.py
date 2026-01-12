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
    
    # Stock
    path('stock/', views.StockOverviewView.as_view(), name='stock_overview'),
    path('stock/adjustment/', views.StockAdjustmentView.as_view(), name='stock_adjustment'),
    path('ledger/', views.InventoryLedgerListView.as_view(), name='inventory_ledger'),
    
    # Shop Pricing (for Shop Managers)
    path('shop-prices/', views.ShopPriceListView.as_view(), name='shop_price_list'),
    path('shop-prices/<int:pk>/set/', views.ShopPriceSetView.as_view(), name='shop_price_set'),
    
    # API
    path('api/batches/', views.get_batches_for_product, name='api_batches'),
    path('api/products/search/', views.search_products, name='api_product_search'),
]
