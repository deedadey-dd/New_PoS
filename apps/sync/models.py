from django.db import models
from apps.core.models import TenantModel, Location

class SyncQueue(TenantModel):
    """
    Queue for items waiting to be synced to the server.
    Used primarily on the client side (Electron), but can be used on server for downstream syncs if needed.
    """
    shop = models.ForeignKey(
        Location, 
        on_delete=models.CASCADE, 
        related_name='sync_queue', 
        limit_choices_to={'location_type': 'SHOP'}
    )
    model_name = models.CharField(max_length=50) # 'Sale', 'Product', 'Inventory'
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    
    synced = models.BooleanField(default=False)
    synced_at = models.DateTimeField(null=True, blank=True)
    
    server_id = models.CharField(max_length=100, null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)
    device_id = models.CharField(max_length=100)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['tenant', 'synced']),
            models.Index(fields=['tenant', 'shop', 'synced']),
        ]
    
    def __str__(self):
        return f"{self.model_name} - {self.device_id} ({'Synced' if self.synced else 'Pending'})"


class SyncLog(TenantModel):
    """
    Log of synchronization history.
    """
    DIRECTION_CHOICES = [
        ('device_to_server', 'Device to Server'),
        ('server_to_device', 'Server to Device'),
    ]
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('conflict', 'Conflict'),
    ]

    # Optional link to a specific Sale if this log is about a Sale
    # Using string reference to avoid circular import if sync app is loaded before sales app
    sale_transaction = models.ForeignKey(
        'sales.Sale', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='sync_logs'
    )
    
    device_id = models.CharField(max_length=100)
    device_type = models.CharField(max_length=50)
    sync_direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    
    entity_type = models.CharField(max_length=50) # 'Sale', 'Product'
    entity_id = models.CharField(max_length=100) # UUID or ID
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_message = models.TextField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'device_id']),
            models.Index(fields=['tenant', 'created_at']),
        ]

    def __str__(self):
        return f"{self.sync_direction} - {self.entity_type} {self.entity_id} ({self.status})"
