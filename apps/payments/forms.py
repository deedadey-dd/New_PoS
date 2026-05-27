from django import forms
from .models import PaymentProviderConfig, ShopPaymentAssignment

class PaymentProviderConfigForm(forms.ModelForm):
    class Meta:
        model = PaymentProviderConfig
        fields = [
            'provider', 'nickname', 'is_active', 'test_mode',
            'public_key', '_secret_key', '_webhook_secret',
            'merchant_id', 'callback_url'
        ]
        widgets = {
            '_secret_key': forms.PasswordInput(render_value=True),
            '_webhook_secret': forms.PasswordInput(render_value=True),
        }
        labels = {
            '_secret_key': 'Secret Key / API Key',
            '_webhook_secret': 'Webhook Secret (Optional)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'


class ShopPaymentAssignmentForm(forms.ModelForm):
    class Meta:
        model = ShopPaymentAssignment
        fields = ['shop', 'provider_config', 'is_default', 'priority']
        
    def __init__(self, *args, **kwargs):
        tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)
        if tenant:
            self.fields['shop'].queryset = self.fields['shop'].queryset.filter(tenant=tenant)
            self.fields['provider_config'].queryset = self.fields['provider_config'].queryset.filter(tenant=tenant)
            
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-check-input'
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
            else:
                field.widget.attrs['class'] = 'form-control'
