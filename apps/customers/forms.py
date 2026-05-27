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
        choices=[('CASH', 'Cash'), ('MOMO', 'Local Momo'), ('ECASH', 'E-Cash'), ('BANK', 'Bank Transfer')],
        widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_payment_method'})
    )
    provider_config = forms.ChoiceField(
        required=False,
        widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_provider_config'})
    )
    customer_phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'id': 'id_customer_phone', 'placeholder': 'For E-Cash (e.g. 024...)'})
    )

    def __init__(self, *args, **kwargs):
        tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)
        
        choices = [('', '--- Select Platform ---')]
        if tenant:
            from apps.payments.models import PaymentProviderConfig
            providers = PaymentProviderConfig.objects.filter(tenant=tenant, is_active=True)
            choices += [(p.id, p.nickname) for p in providers]
        self.fields['provider_config'].choices = choices
