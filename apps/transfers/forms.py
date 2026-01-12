"""
Forms for the transfers app.
"""
from django import forms
from django.forms import inlineformset_factory
from decimal import Decimal

from .models import Transfer, TransferItem
from apps.core.models import Location
from apps.inventory.models import Product, Batch


class TransferForm(forms.ModelForm):
    """Form for creating/editing transfers."""
    
    # Transfer direction rules by location type:
    # PRODUCTION → STORES only
    # STORES → SHOP or PRODUCTION
    # SHOP → STORES only (cannot transfer to other shops)
    
    TRANSFER_RULES = {
        'PRODUCTION': ['STORES'],      # Production can only transfer to Stores
        'STORES': ['SHOP', 'PRODUCTION'],  # Stores can transfer to Shops or back to Production
        'SHOP': ['STORES'],            # Shop can only transfer back to Stores
    }
    
    class Meta:
        model = Transfer
        fields = ['source_location', 'destination_location', 'notes']
        widgets = {
            'source_location': forms.HiddenInput(),
            'destination_location': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }
    # Map roles to their default location types
    ROLE_LOCATION_MAP = {
        'PRODUCTION_MANAGER': 'PRODUCTION',
        'STORES_MANAGER': 'STORES',
        'SHOP_MANAGER': 'SHOP',
    }
    
    def __init__(self, *args, tenant=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.user = user
        self.source_location_display = None
        
        if tenant:
            # Default: filter all locations by tenant
            self.fields['destination_location'].queryset = Location.objects.filter(
                tenant=tenant, is_active=True
            )
        
        if not user or self.instance.pk:
            return
        
        source_location = None
        
        # Try to use user's assigned location first
        if user.location:
            source_location = user.location
        # Otherwise, find location based on user's role
        elif user.role and tenant:
            role_name = user.role.name
            location_type = self.ROLE_LOCATION_MAP.get(role_name)
            
            if location_type:
                # Find first matching location of the appropriate type for this tenant
                source_location = Location.objects.filter(
                    tenant=tenant,
                    is_active=True,
                    location_type=location_type
                ).first()
        
        # If we found a source location, set it up
        if source_location:
            self.initial['source_location'] = source_location.pk
            self.source_location_display = source_location
            
            # Get allowed destination types based on source location type
            source_type = source_location.location_type
            allowed_dest_types = self.TRANSFER_RULES.get(source_type, [])
            
            # Filter destinations to valid types only
            if tenant and allowed_dest_types:
                valid_destinations = Location.objects.filter(
                    tenant=tenant, 
                    is_active=True,
                    location_type__in=allowed_dest_types
                ).exclude(pk=source_location.pk)
                
                self.fields['destination_location'].queryset = valid_destinations
                
                # Auto-select first valid destination if only one exists
                if valid_destinations.count() == 1:
                    self.initial['destination_location'] = valid_destinations.first().pk
    
    def clean(self):
        cleaned_data = super().clean()
        source = cleaned_data.get('source_location')
        destination = cleaned_data.get('destination_location')
        
        if source and destination:
            if source == destination:
                raise forms.ValidationError("Source and destination locations must be different.")
            
            # Validate transfer direction
            allowed_dest_types = self.TRANSFER_RULES.get(source.location_type, [])
            if destination.location_type not in allowed_dest_types:
                allowed_names = ', '.join(allowed_dest_types) or 'none'
                raise forms.ValidationError(
                    f"Transfers from {source.location_type} can only go to: {allowed_names}. "
                    f"Cannot transfer to {destination.location_type}."
                )
        
        return cleaned_data


class TransferItemForm(forms.ModelForm):
    """Form for transfer items."""
    
    # Extra field for production workflow - enter total cost instead of unit cost
    total_cost = forms.DecimalField(
        required=False,
        min_value=Decimal('0'),
        widget=forms.NumberInput(attrs={
            'class': 'form-control total-cost-input',
            'min': '0',
            'step': '0.01',
            'placeholder': 'Total cost for all units'
        })
    )
    
    class Meta:
        model = TransferItem
        fields = ['product', 'batch', 'quantity_requested', 'unit_cost', 'notes']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select product-select'}),
            'batch': forms.Select(attrs={'class': 'form-select batch-select'}),
            'quantity_requested': forms.NumberInput(attrs={'class': 'form-control quantity-input', 'min': '0.01', 'step': '0.01'}),
            'unit_cost': forms.NumberInput(attrs={'class': 'form-control unit-cost-input', 'min': '0', 'step': '0.01', 'placeholder': 'Auto from batch'}),
            'notes': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional notes'}),
        }
    
    def __init__(self, *args, tenant=None, source_location=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_location = source_location
        self.source_type = source_location.location_type if source_location else None
        
        # Make unit_cost optional - will be calculated or set from batch
        self.fields['unit_cost'].required = False
        self.fields['batch'].required = False
        
        if tenant:
            self.fields['product'].queryset = Product.objects.filter(
                tenant=tenant, is_active=True
            )
            # Initially empty - will be populated by JavaScript based on product selection
            self.fields['batch'].queryset = Batch.objects.none()
        
        if source_location:
            # Filter batches by source location if provided
            self.fields['batch'].queryset = Batch.objects.filter(
                tenant=tenant,
                location=source_location,
                status='AVAILABLE',
                current_quantity__gt=0
            )
    
    def clean(self):
        cleaned_data = super().clean()
        quantity = cleaned_data.get('quantity_requested')
        total_cost = cleaned_data.get('total_cost')
        unit_cost = cleaned_data.get('unit_cost')
        
        # If total_cost is provided and quantity exists, calculate unit_cost
        if total_cost and quantity and quantity > 0:
            cleaned_data['unit_cost'] = total_cost / quantity
        
        # If still no unit_cost, try to get from batch
        if not cleaned_data.get('unit_cost'):
            batch = cleaned_data.get('batch')
            if batch and batch.unit_cost:
                cleaned_data['unit_cost'] = batch.unit_cost
        
        return cleaned_data


# Formset for transfer items - start with 1 empty row
TransferItemFormSet = inlineformset_factory(
    Transfer,
    TransferItem,
    form=TransferItemForm,
    extra=0,  # Start with 0 extra, we add 1 in the view
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class TransferReceiveForm(forms.Form):
    """Form for receiving transfer items."""
    
    def __init__(self, *args, transfer=None, **kwargs):
        super().__init__(*args, **kwargs)
        
        if transfer:
            for item in transfer.items.all():
                self.fields[f'received_{item.pk}'] = forms.DecimalField(
                    label=f"{item.product.name}",
                    initial=item.quantity_sent,
                    min_value=Decimal('0'),
                    max_value=item.quantity_sent,
                    widget=forms.NumberInput(attrs={
                        'class': 'form-control',
                        'min': '0',
                        'max': str(item.quantity_sent),
                        'step': '0.01'
                    })
                )


class TransferDisputeForm(forms.Form):
    """Form for disputing a transfer."""
    
    dispute_reason = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Describe the reason for the dispute...'
        }),
        min_length=10
    )


class TransferCloseForm(forms.Form):
    """Form for closing a transfer."""
    
    resolution_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'Resolution notes (optional)...'
        })
    )


# ==================== Stock Request Forms ====================

from .models import StockRequest, StockRequestItem


class StockRequestForm(forms.ModelForm):
    """Form for creating/editing stock requests."""
    
    # Request rules: who can request from whom (opposite of transfer rules)
    REQUEST_RULES = {
        'SHOP': ['STORES'],       # Shop can request from Stores
        'STORES': ['PRODUCTION'], # Stores can request from Production
    }
    
    # Map roles to their default location types
    ROLE_LOCATION_MAP = {
        'PRODUCTION_MANAGER': 'PRODUCTION',
        'STORES_MANAGER': 'STORES',
        'SHOP_MANAGER': 'SHOP',
    }
    
    class Meta:
        model = StockRequest
        fields = ['requesting_location', 'supplying_location', 'notes']
        widgets = {
            'requesting_location': forms.HiddenInput(),
            'supplying_location': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Reason for request...'}),
        }
    
    def __init__(self, *args, tenant=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.user = user
        self.requesting_location_display = None
        
        if tenant:
            self.fields['supplying_location'].queryset = Location.objects.filter(
                tenant=tenant, is_active=True
            )
        
        if not user or self.instance.pk:
            return
        
        requesting_location = None
        
        # Determine user's location
        if user.location:
            requesting_location = user.location
        elif user.role and tenant:
            role_name = user.role.name
            location_type = self.ROLE_LOCATION_MAP.get(role_name)
            
            if location_type:
                requesting_location = Location.objects.filter(
                    tenant=tenant,
                    is_active=True,
                    location_type=location_type
                ).first()
        
        if requesting_location:
            self.initial['requesting_location'] = requesting_location.pk
            self.requesting_location_display = requesting_location
            
            # Get allowed supplier types based on requesting location type
            req_type = requesting_location.location_type
            allowed_supplier_types = self.REQUEST_RULES.get(req_type, [])
            
            # Filter suppliers to valid types only
            if tenant and allowed_supplier_types:
                valid_suppliers = Location.objects.filter(
                    tenant=tenant,
                    is_active=True,
                    location_type__in=allowed_supplier_types
                ).exclude(pk=requesting_location.pk)
                
                self.fields['supplying_location'].queryset = valid_suppliers
                
                # Auto-select first valid supplier if only one exists
                if valid_suppliers.count() == 1:
                    self.initial['supplying_location'] = valid_suppliers.first().pk
    
    def clean(self):
        cleaned_data = super().clean()
        requesting = cleaned_data.get('requesting_location')
        supplying = cleaned_data.get('supplying_location')
        
        if requesting and supplying:
            if requesting == supplying:
                raise forms.ValidationError("Requesting and supplying locations must be different.")
            
            # Validate request direction
            allowed_supplier_types = self.REQUEST_RULES.get(requesting.location_type, [])
            if supplying.location_type not in allowed_supplier_types:
                allowed_names = ', '.join(allowed_supplier_types) or 'none'
                raise forms.ValidationError(
                    f"Requests from {requesting.location_type} can only be to: {allowed_names}. "
                    f"Cannot request from {supplying.location_type}."
                )
        
        return cleaned_data


class StockRequestItemForm(forms.ModelForm):
    """Form for stock request items."""
    
    class Meta:
        model = StockRequestItem
        fields = ['product', 'quantity_requested', 'notes']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select product-select'}),
            'quantity_requested': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'step': '0.01',
                'placeholder': '0 = any quantity'
            }),
            'notes': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional notes'}),
        }
    
    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['quantity_requested'].required = False
        
        if tenant:
            self.fields['product'].queryset = Product.objects.filter(
                tenant=tenant, is_active=True
            )


# Formset for stock request items
StockRequestItemFormSet = inlineformset_factory(
    StockRequest,
    StockRequestItem,
    form=StockRequestItemForm,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class StockRequestRejectForm(forms.Form):
    """Form for rejecting a stock request."""
    
    rejection_reason = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Reason for rejection...'
        }),
        min_length=10
    )

