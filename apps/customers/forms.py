from django import forms
from .models import Customer, CustomerTransaction

class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['shop', 'name', 'phone', 'email', 'address', 'credit_limit', 'is_active']
        widgets = {
            'address': forms.Textarea(attrs={'rows': 3}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
        self.fields['is_active'].widget.attrs.update({'class': 'form-check-input'})

class CustomerPaymentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        min_value=0.01,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'})
    )
    description = forms.CharField(
        max_length=255, 
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Payment description'})
    )
    payment_method = forms.ChoiceField(
        choices=[('CASH', 'Cash'), ('ECASH', 'E-Cash'), ('BANK', 'Bank Transfer')],
        widget=forms.Select(attrs={'class': 'form-control'})
    )
