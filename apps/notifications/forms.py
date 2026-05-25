from django import forms
from .models import BulletinPost
from apps.core.models import Location

class BulletinPostForm(forms.ModelForm):
    class Meta:
        model = BulletinPost
        fields = ['title', 'body', 'post_type', 'target_roles', 'target_locations']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Message Title'}),
            'body': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Type your message here...'}),
            'post_type': forms.Select(attrs={'class': 'form-select'}),
            'target_roles': forms.SelectMultiple(attrs={'class': 'form-select tom-select'}),
            'target_locations': forms.SelectMultiple(attrs={'class': 'form-select tom-select'}),
        }
        
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and user.tenant:
            from apps.core.models import Role
            self.fields['target_locations'].queryset = Location.objects.filter(
                tenant=user.tenant, is_active=True
            )
            self.fields['target_roles'].queryset = Role.objects.exclude(name__in=['ADMIN', 'SUPER_ADMIN', 'TENANT_MANAGER'])
            
            # Restrict target_roles and target_locations choices based on who is posting
            role_name = user.role.name if user.role else None
            
            if role_name == 'SHOP_MANAGER':
                # Shop managers can only target their own shop's staff
                # Or other shop managers if inter-shop transfers is allowed
                allowed_roles = ['SHOP_ATTENDANT']
                
                if user.tenant.allow_inter_shop_transfers:
                    allowed_roles.append('SHOP_MANAGER')
                    
                self.fields['target_roles'].queryset = Role.objects.filter(name__in=allowed_roles)
                
                # They can only target their own location or other shops if allowed
                if user.tenant.allow_inter_shop_transfers:
                    self.fields['target_locations'].queryset = Location.objects.filter(
                        tenant=user.tenant, is_active=True, location_type='SHOP'
                    )
                elif user.location:
                    self.fields['target_locations'].queryset = Location.objects.filter(id=user.location.id)
                    self.initial['target_locations'] = [user.location.id]
                    
            elif role_name != 'ADMIN':
                # Other non-admins (e.g. Accountant) might be restricted in locations
                # We can leave this open for accountants/stores managers for now
                pass
