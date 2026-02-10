from rest_framework import serializers
from apps.sales.models import Sale, SaleItem
from apps.inventory.models import Product, Inventory, Batch
from apps.sync.models import SyncQueue, SyncLog

class SaleItemSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    
    class Meta:
        model = SaleItem
        exclude = ['sale'] # Exclude backref to avoid circularity in validation if needed
        extra_kwargs = {
            'id': {'read_only': False, 'required': False}, # Allow sending ID if preserving
            'tenant': {'required': False},
        }

class TransactionSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True)
    
    class Meta:
        model = Sale
        fields = '__all__'
        extra_kwargs = {
            'tenant': {'required': False}, # Inferred from request usually
            'shop': {'required': False}, 
            'attendant': {'required': False},
            'sale_number': {'required': False}, # Auto-generated
        }

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        # Ensure 'tenant' is set from context if not in data
        if 'tenant' not in validated_data and 'request' in self.context:
             validated_data['tenant'] = self.context['request'].user.tenant
        
        sale = Sale.objects.create(**validated_data)
        
        for item_data in items_data:
            # item_data might lack tenant if it was excluded from validation or marked required=False
            # But the model field requires it. We must inject it.
            if 'tenant' not in item_data:
                item_data['tenant'] = sale.tenant
            
            # Handle ForeignKeys if sent as IDs or objects
            # Assuming product is sent as ID
            SaleItem.objects.create(sale=sale, **item_data)
        
        sale.calculate_totals()
        return sale

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'

class InventorySerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    
    class Meta:
        model = Inventory
        fields = '__all__'

class SyncQueueSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncQueue
        fields = '__all__'

class SyncLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyncLog
        fields = '__all__'
