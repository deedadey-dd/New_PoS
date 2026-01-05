"""
Forms for inventory app.
"""
from django import forms
from django.core.exceptions import ValidationError
from decimal import Decimal

from .models import Category, Product, Batch, InventoryLedger, ShopPrice
from apps.core.models import Location


class CategoryForm(forms.ModelForm):
    """Form for creating/editing categories."""
    
    class Meta:
        model = Category
        fields = ['name', 'description', 'parent', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Category Name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Description'}),
            'parent': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    
    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant:
            self.fields['parent'].queryset = Category.objects.filter(tenant=tenant, is_active=True)
        else:
            self.fields['parent'].queryset = Category.objects.none()
        
        # Default is_active to True for new categories
        if not self.instance.pk:
            self.initial['is_active'] = True


class ProductForm(forms.ModelForm):
    """Form for creating/editing products."""
    
    class Meta:
        model = Product
        fields = ['sku', 'name', 'description', 'category', 'unit_of_measure', 
                  'default_selling_price', 'reorder_level', 'image', 'is_active']
        widgets = {
            'sku': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'SKU/Barcode'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Product Name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Description'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'unit_of_measure': forms.Select(attrs={'class': 'form-select'}),
            'default_selling_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'reorder_level': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    
    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant:
            self.fields['category'].queryset = Category.objects.filter(tenant=tenant, is_active=True)
        else:
            self.fields['category'].queryset = Category.objects.none()


class BatchForm(forms.ModelForm):
    """Form for creating batches (receiving stock)."""
    
    class Meta:
        model = Batch
        fields = ['product', 'location', 'batch_number', 'unit_cost', 'initial_quantity',
                  'manufacture_date', 'expiry_date', 'notes']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'batch_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Batch/Lot Number'}),
            'unit_cost': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'initial_quantity': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
            'manufacture_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'expiry_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Notes'}),
        }
    
    def __init__(self, *args, tenant=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.auto_location = None
        
        if tenant:
            self.fields['product'].queryset = Product.objects.filter(tenant=tenant, is_active=True)
            # Only Production and Stores can receive batches
            self.fields['location'].queryset = Location.objects.filter(
                tenant=tenant, 
                is_active=True,
                location_type__in=['PRODUCTION', 'STORES']
            )
        else:
            self.fields['product'].queryset = Product.objects.none()
            self.fields['location'].queryset = Location.objects.none()
        
        # Auto-set location based on user's assigned location
        if user and user.location and user.location.location_type in ['PRODUCTION', 'STORES']:
            self.initial['location'] = user.location.pk
            self.auto_location = user.location
            # Make location field hidden when auto-set
            self.fields['location'].widget = forms.HiddenInput()
        elif user and tenant:
            # For users without location, find first Production or Stores
            auto_loc = Location.objects.filter(
                tenant=tenant,
                is_active=True,
                location_type__in=['PRODUCTION', 'STORES']
            ).first()
            if auto_loc:
                self.initial['location'] = auto_loc.pk
                self.auto_location = auto_loc
                self.fields['location'].widget = forms.HiddenInput()
    
    def clean(self):
        cleaned_data = super().clean()
        manufacture_date = cleaned_data.get('manufacture_date')
        expiry_date = cleaned_data.get('expiry_date')
        
        if manufacture_date and expiry_date and expiry_date <= manufacture_date:
            raise ValidationError("Expiry date must be after manufacture date.")
        
        return cleaned_data


class StockAdjustmentForm(forms.Form):
    """Form for stock adjustments."""
    ADJUSTMENT_TYPES = [
        ('ADJUST', 'Adjustment'),
        ('DAMAGE', 'Damage/Write-off'),
    ]
    
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    batch = forms.ModelChoiceField(
        queryset=Batch.objects.none(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    adjustment_type = forms.ChoiceField(
        choices=ADJUSTMENT_TYPES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text="Positive to add, negative to remove"
    )
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Reason for adjustment'}),
        required=True
    )
    
    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant:
            self.fields['product'].queryset = Product.objects.filter(tenant=tenant, is_active=True)
            self.fields['location'].queryset = Location.objects.filter(tenant=tenant, is_active=True)


class ShopPriceForm(forms.ModelForm):
    """Form for setting shop-specific pricing."""
    
    class Meta:
        model = ShopPrice
        fields = ['product', 'location', 'selling_price', 'min_margin_percent', 'is_active']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'selling_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
            'min_margin_percent': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    
    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant:
            self.fields['product'].queryset = Product.objects.filter(tenant=tenant, is_active=True)
            self.fields['location'].queryset = Location.objects.filter(
                tenant=tenant, 
                is_active=True,
                location_type='SHOP'
            )
