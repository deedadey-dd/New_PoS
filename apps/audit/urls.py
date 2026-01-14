from django.urls import path
from . import views

app_name = 'audit'

urlpatterns = [
    # Product Lifecycle Tracking
    path('product-lifecycle/', views.ProductLifecycleView.as_view(), name='product_lifecycle'),
    path('product-lifecycle/<int:pk>/', views.ProductLifecycleView.as_view(), name='product_lifecycle_detail'),
    
    # Profit/Loss Reports
    path('profit-loss/products/', views.ProductProfitLossView.as_view(), name='product_profit_loss'),
    path('profit-loss/locations/', views.LocationProfitLossView.as_view(), name='location_profit_loss'),
    path('profit-loss/managers/', views.ManagerProfitLossView.as_view(), name='manager_profit_loss'),
    
    # Inventory Movement Report
    path('inventory-movements/', views.InventoryMovementReportView.as_view(), name='inventory_movements'),
]
