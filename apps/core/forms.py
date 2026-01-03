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
        fields = ['name', 'location_type', 'address', 'phone', 'email']
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
