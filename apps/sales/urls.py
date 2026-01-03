"""
URL configuration for the sales app.
"""
from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    # POS Interface
    path('pos/', views.POSView.as_view(), name='pos'),
    
    # Shift Management
    path('shift/open/', views.ShiftOpenView.as_view(), name='shift_open'),
    path('shift/<int:pk>/close/', views.ShiftCloseView.as_view(), name='shift_close'),
    
    # Sales History
    path('', views.SaleListView.as_view(), name='sale_list'),
    path('<int:pk>/receipt/', views.SaleDetailView.as_view(), name='sale_receipt'),
    
    # API Endpoints
    path('api/products/search/', views.api_product_search, name='api_product_search'),
    path('api/checkout/', views.api_complete_sale, name='api_checkout'),
    path('api/<int:pk>/void/', views.api_void_sale, name='api_void_sale'),
]
