"""
Forms for the sales app.
"""
from django import forms
from .models import ShopSettings


class ShopManagerSettingsForm(forms.ModelForm):
    """
    Form for Shop Managers to configure their shop's receipt, printing, and PoS display settings.
    Explicitly excludes payment configuration fields.
    """
    class Meta:
        model = ShopSettings
        fields = [
            'receipt_printer_type',
            'show_logo_on_receipt',
            'receipt_print_copies',
            'receipt_header',
            'receipt_footer',
            'hide_zero_stock_items',
            'warn_on_low_stock',
        ]
        widgets = {
            'receipt_printer_type': forms.Select(attrs={'class': 'form-select'}),
            'show_logo_on_receipt': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'receipt_print_copies': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 10}),
            'receipt_header': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional custom header text'}),
            'receipt_footer': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Thank you! Come again.'}),
            'hide_zero_stock_items': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'warn_on_low_stock': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AdminShopPaymentSettingsForm(forms.ModelForm):
    """
    Form for Admins to configure payment methods and API keys for a specific shop.
    """
    class Meta:
        model = ShopSettings
        fields = [
            'enable_cash_payment',
            'enable_credit_payment',
            'enable_ecash_payment',
            'paystack_public_key',
            'paystack_secret_key',
        ]
        widgets = {
            'enable_cash_payment': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'enable_credit_payment': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'enable_ecash_payment': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'paystack_public_key': forms.TextInput(attrs={
                'class': 'form-control', 
                'placeholder': 'pk_live_... or pk_test_...'
            }),
            'paystack_secret_key': forms.PasswordInput(attrs={
                'class': 'form-control', 
                'placeholder': 'sk_live_... or sk_test_...',
                'render_value': True
            }),
        }
        help_texts = {
            'paystack_public_key': 'Leave blank to use the global Tenant Paystack keys.',
            'paystack_secret_key': 'Leave blank to use the global Tenant Paystack keys.',
        }
