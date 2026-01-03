"""
Admin configuration for core app.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Tenant, Location, Role, User


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'phone', 'currency', 'is_active', 'created_at']
    list_filter = ['is_active', 'currency']
    search_fields = ['name', 'email']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'tenant', 'location_type', 'phone', 'is_active']
    list_filter = ['location_type', 'is_active', 'tenant']
    search_fields = ['name', 'address']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'can_manage_users', 'can_manage_inventory', 'can_manage_sales']
    list_filter = ['can_manage_users', 'can_manage_inventory']


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['email', 'first_name', 'last_name', 'tenant', 'role', 'is_active']
    list_filter = ['is_active', 'tenant', 'role']
    search_fields = ['email', 'first_name', 'last_name']
    ordering = ['email']
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone', 'profile_image')}),
        ('Organization', {'fields': ('tenant', 'role', 'location', 'is_tenant_setup_complete')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'password1', 'password2', 'tenant', 'role'),
        }),
    )
