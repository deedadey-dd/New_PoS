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
    
    # API
    path('api/batches/', views.get_batches_for_product, name='api_batches'),
]
