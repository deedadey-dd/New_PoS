"""
Forms for inventory app.
"""
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
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
    
    def _generate_batch_number(self, tenant):
        """Generate a date-based batch number (YYYYMMDD), appending a suffix if needed."""
        today = timezone.now().date()
        base_number = today.strftime('%Y%m%d')
        
        # Check if this base number already exists for this tenant
        existing = Batch.objects.filter(
            tenant=tenant,
            batch_number__startswith=base_number
        ).values_list('batch_number', flat=True)
        
        if not existing:
            return base_number
        
        # Find the highest suffix
        max_suffix = 0
        for bn in existing:
            if bn == base_number:
                max_suffix = max(max_suffix, 1)
            elif bn.startswith(base_number + '-'):
                try:
                    suffix = int(bn.split('-')[-1])
                    max_suffix = max(max_suffix, suffix)
                except ValueError:
                    pass
        
        return f"{base_number}-{str(max_suffix + 1).zfill(2)}"
    
    def __init__(self, *args, tenant=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.tenant = tenant
        self.auto_location = None
        
        # Determine allowed location types based on role
        allowed_types = ['PRODUCTION', 'STORES']
        if user and user.role:
            if user.role.name == 'STORES_MANAGER':
                allowed_types = ['STORES']
            elif user.role.name == 'PRODUCTION_MANAGER':
                allowed_types = ['PRODUCTION']
        
        if tenant:
            self.fields['product'].queryset = Product.objects.filter(tenant=tenant, is_active=True)
            
            # Only Production and Stores can receive batches
            self.fields['location'].queryset = Location.objects.filter(
                tenant=tenant, 
                is_active=True,
                location_type__in=allowed_types
            )
            
            # Auto-generate batch number if creating a new batch
            if not self.instance.pk:
                self.initial['batch_number'] = self._generate_batch_number(tenant)
        else:
            self.fields['product'].queryset = Product.objects.none()
            self.fields['location'].queryset = Location.objects.none()
        
        # Auto-set location based on user's assigned location
        if user and user.location and user.location.location_type in allowed_types:
            self.initial['location'] = user.location.pk
            self.auto_location = user.location
            # Make location field hidden when auto-set
            self.fields['location'].widget = forms.HiddenInput()
        elif user and tenant:
            # For users without location, find first allowed location
            auto_loc = Location.objects.filter(
                tenant=tenant,
                is_active=True,
                location_type__in=allowed_types
            ).first()
            if auto_loc:
                self.initial['location'] = auto_loc.pk
                self.auto_location = auto_loc
                self.fields['location'].widget = forms.HiddenInput()
    
    def clean(self):
        cleaned_data = super().clean()
        manufacture_date = cleaned_data.get('manufacture_date')
        expiry_date = cleaned_data.get('expiry_date')
        batch_number = cleaned_data.get('batch_number')
        product = cleaned_data.get('product')
        location = cleaned_data.get('location')
        
        if manufacture_date and expiry_date and expiry_date <= manufacture_date:
            raise ValidationError("Expiry date must be after manufacture date.")
        
        # Check for duplicate batch number (same tenant + product + batch_number + location)
        if batch_number and product and location and self.tenant:
            duplicate_qs = Batch.objects.filter(
                tenant=self.tenant,
                product=product,
                batch_number=batch_number,
                location=location
            )
            # Exclude current instance when editing
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            
            if duplicate_qs.exists():
                raise ValidationError(
                    f'A batch with number "{batch_number}" already exists for this product '
                    f'at {location.name}. Please use a different batch number.'
                )
        
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
