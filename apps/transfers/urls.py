"""
URL configuration for the transfers app.
"""
from django.urls import path
from . import views

app_name = 'transfers'

urlpatterns = [
    # Transfer List and CRUD
    path('', views.TransferListView.as_view(), name='transfer_list'),
    path('create/', views.TransferCreateView.as_view(), name='transfer_create'),
    path('products/', views.TransferItemHistoryView.as_view(), name='transfer_item_history'),
    path('<int:pk>/', views.TransferDetailView.as_view(), name='transfer_detail'),
    
    # Transfer Actions
    path('<int:pk>/send/', views.TransferSendView.as_view(), name='transfer_send'),
    path('<int:pk>/receive/', views.TransferReceiveView.as_view(), name='transfer_receive'),
    path('<int:pk>/dispute/', views.TransferDisputeView.as_view(), name='transfer_dispute'),
    path('<int:pk>/close/', views.TransferCloseView.as_view(), name='transfer_close'),
    path('<int:pk>/cancel/', views.TransferCancelView.as_view(), name='transfer_cancel'),
    
    # Transfer API
    path('api/batches/', views.get_batches_for_transfer, name='api_batches'),
    path('api/batch-details/', views.get_batch_details, name='api_batch_details'),
    
    # Stock Requests
    path('requests/', views.StockRequestListView.as_view(), name='stock_request_list'),
    path('requests/create/', views.StockRequestCreateView.as_view(), name='stock_request_create'),
    path('requests/<int:pk>/', views.StockRequestDetailView.as_view(), name='stock_request_detail'),
    path('requests/<int:pk>/approve/', views.StockRequestApproveView.as_view(), name='stock_request_approve'),
    path('requests/<int:pk>/reject/', views.StockRequestRejectView.as_view(), name='stock_request_reject'),
    path('requests/<int:pk>/convert/', views.StockRequestConvertView.as_view(), name='stock_request_convert'),
    path('requests/<int:pk>/cancel/', views.StockRequestCancelView.as_view(), name='stock_request_cancel'),
]

