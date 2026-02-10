from django.urls import path
from . import views

urlpatterns = [
    # Transactions
    path('transactions/', views.SyncTransactionView.as_view(), name='sync-transaction'),
    
    # Sync specific
    path('sync/batch/', views.SyncBatchView.as_view(), name='sync-batch'),
    path('sync/changes/', views.GetUpdatesView.as_view(), name='get-updates'),
    path('sync/status/', views.check_sync_status, name='sync-status'),
    
    # System
    path('health/', views.health_check, name='health-check'),
]
