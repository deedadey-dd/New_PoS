"""
Forms for the core app.
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth import get_user_model
from .models import Tenant, Location, Role

User = get_user_model()


class LoginForm(AuthenticationForm):
    """Custom login form with styled fields."""
    username = forms.EmailField(
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Email address',
            'autofocus': True,
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Password',
        })
    )


class TenantSetupForm(forms.ModelForm):
    """Form for setting up a new tenant."""
    
    class Meta:
        model = Tenant
        fields = ['name', 'email', 'phone', 'address', 'currency']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Organization Name',
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Organization Email',
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Phone Number',
            }),
            'address': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Business Address',
                'rows': 2,
            }),
            'currency': forms.Select(attrs={
                'class': 'form-select',
            }),
        }


class LocationForm(forms.ModelForm):
    """Form for creating/editing locations."""
    
    class Meta:
        model = Location
        fields = ['name', 'location_type', 'address', 'phone', 'email', 'receipt_copies']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Location Name',
            }),
            'location_type': forms.Select(attrs={
                'class': 'form-select',
            }),
            'address': forms.Textarea(attrs={
                'class': 'form-control',
                'placeholder': 'Address',
                'rows': 2,
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Phone',
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Email',
            }),
            'receipt_copies': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 1,
                'max': 5,
            }),
        }


class UserCreateForm(UserCreationForm):
    """Form for creating new users."""
    
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'phone', 'role', 'location']
        widgets = {
            'email': forms.EmailInput(attrs={
                'class': 'form-control',
                'placeholder': 'Email Address',
            }),
            'first_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'First Name',
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Last Name',
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Phone',
            }),
            'role': forms.Select(attrs={
                'class': 'form-select',
            }),
            'location': forms.Select(attrs={
                'class': 'form-select',
            }),
        }
    
    def __init__(self, *args, tenant=None, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Password'})
        self.fields['password2'].widget.attrs.update({'class': 'form-control', 'placeholder': 'Confirm Password'})
        
        # Filter roles based on current user's permissions
        # Only Super Admin (platform level) can create Admin users
        excluded_roles = ['SUPER_ADMIN']
        if current_user and not current_user.is_superuser:
            # Non-superusers (including tenant Admins) cannot create Admin users
            excluded_roles.append('ADMIN')
        
        self.fields['role'].queryset = Role.objects.exclude(name__in=excluded_roles)
        
        # Filter locations by tenant
        if tenant:
            self.fields['location'].queryset = Location.objects.filter(tenant=tenant)
        else:
            self.fields['location'].queryset = Location.objects.none()


class UserEditForm(forms.ModelForm):
    """Form for editing existing users."""
    
    class Meta:
        model = User
        fields = ['email', 'first_name', 'last_name', 'phone', 'role', 'location', 'is_active']
        widgets = {
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-select'}),
            'location': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
    
    def __init__(self, *args, tenant=None, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Filter roles based on current user's permissions
        # Only Super Admin (platform level) can assign Admin role
        excluded_roles = ['SUPER_ADMIN']
        if current_user and not current_user.is_superuser:
            # Non-superusers cannot assign Admin role
            excluded_roles.append('ADMIN')
        
        self.fields['role'].queryset = Role.objects.exclude(name__in=excluded_roles)
        
        if tenant:
            self.fields['location'].queryset = Location.objects.filter(tenant=tenant)


class TenantSettingsForm(forms.ModelForm):
    """Form for editing tenant settings."""
    
    class Meta:
        model = Tenant
        fields = [
            'name', 'email', 'phone', 'address', 'currency',
            'allow_negative_stock', 'require_refund_approval', 'require_return_approval',
            'credit_limit_warning_percent', 'backdating_allowed_days',
            'shop_manager_can_add_products', 'shop_manager_can_receive_stock',
            'allow_accountant_to_shop_transfers',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'credit_limit_warning_percent': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 100}),
            'backdating_allowed_days': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'allow_negative_stock': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'require_refund_approval': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'require_return_approval': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'shop_manager_can_add_products': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'shop_manager_can_receive_stock': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AdminPasswordResetForm(forms.Form):
    """Form for admins to reset a user's password."""
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'New Password',
        }),
        min_length=8,
    )
    new_password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm Password',
        }),
    )
    
    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('new_password1')
        password2 = cleaned_data.get('new_password2')
        
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Passwords do not match.")
        
        return cleaned_data


class ForcedPasswordChangeForm(forms.Form):
    """Form for users to change password on first login after reset."""
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter new password',
            'autofocus': True,
        }),
        min_length=8,
        help_text="Password must be at least 8 characters."
    )
    new_password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm new password',
        }),
    )
    
    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('new_password1')
        password2 = cleaned_data.get('new_password2')
        
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Passwords do not match.")
        
        return cleaned_data


class AdminOnlyPasswordResetForm(forms.Form):
    """
    Password reset form that only allows Admin-role users to reset via email.
    Regular tenant users must have their passwords reset by their Admin.
    """
    email = forms.EmailField(
        label="Email Address",
        max_length=254,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'autocomplete': 'email',
            'placeholder': 'Enter your email address',
        })
    )
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        
        # Check if user exists and is an Admin
        try:
            user = User.objects.get(email=email)
            if not user.role or user.role.name != 'ADMIN':
                raise forms.ValidationError(
                    "Email-based password reset is only available for administrators. "
                    "Please contact your administrator to reset your password."
                )
        except User.DoesNotExist:
            # Don't reveal whether user exists - generic message
            pass
        
        return email
    
    def get_users(self, email):
        """Given an email, return matching Admin user(s) who can reset password."""
        active_users = User.objects.filter(
            email__iexact=email,
            is_active=True,
            role__name='ADMIN'
        )
        return (u for u in active_users if u.has_usable_password())

