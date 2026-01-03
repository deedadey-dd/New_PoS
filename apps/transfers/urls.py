"""
URL configuration for the transfers app.
"""
from django.urls import path
from . import views

app_name = 'transfers'

urlpatterns = [
    # List and CRUD
    path('', views.TransferListView.as_view(), name='transfer_list'),
    path('create/', views.TransferCreateView.as_view(), name='transfer_create'),
    path('<int:pk>/', views.TransferDetailView.as_view(), name='transfer_detail'),
    
    # Actions
    path('<int:pk>/send/', views.TransferSendView.as_view(), name='transfer_send'),
    path('<int:pk>/receive/', views.TransferReceiveView.as_view(), name='transfer_receive'),
    path('<int:pk>/dispute/', views.TransferDisputeView.as_view(), name='transfer_dispute'),
    path('<int:pk>/close/', views.TransferCloseView.as_view(), name='transfer_close'),
    path('<int:pk>/cancel/', views.TransferCancelView.as_view(), name='transfer_cancel'),
    
    # API
    path('api/batches/', views.get_batches_for_transfer, name='api_batches'),
]
