"""
Views for the core app.
Handles authentication, tenant setup, and user/location management.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.db.models import Count

from .models import Tenant, Location, Role, User
from .forms import LoginForm, TenantSetupForm, LocationForm, UserCreateForm, UserEditForm
from .decorators import admin_required


class LoginView(View):
    """Handle user login."""
    template_name = 'core/login.html'
    
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('core:dashboard')
        form = LoginForm()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            
            # Check if admin needs tenant setup
            if user.is_admin and not user.tenant:
                return redirect('core:tenant_setup')
            
            messages.success(request, f'Welcome back, {user.get_full_name() or user.email}!')
            return redirect('core:dashboard')
        
        return render(request, self.template_name, {'form': form})


class LogoutView(View):
    """Handle user logout."""
    
    def get(self, request):
        logout(request)
        messages.info(request, 'You have been logged out.')
        return redirect('core:login')


class TenantSetupView(LoginRequiredMixin, View):
    """Handle tenant setup for new admin users."""
    template_name = 'core/tenant_setup.html'
    
    def get(self, request):
        # Only allow admins without tenant
        if not request.user.is_admin or request.user.tenant:
            return redirect('core:dashboard')
        
        form = TenantSetupForm()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        if not request.user.is_admin or request.user.tenant:
            return redirect('core:dashboard')
        
        form = TenantSetupForm(request.POST)
        if form.is_valid():
            # Create tenant
            tenant = form.save()
            
            # Link admin to tenant
            request.user.tenant = tenant
            request.user.is_tenant_setup_complete = True
            request.user.save()
            
            messages.success(request, f'Organization "{tenant.name}" has been set up successfully!')
            return redirect('core:dashboard')
        
        return render(request, self.template_name, {'form': form})


class DashboardView(LoginRequiredMixin, View):
    """Main dashboard view."""
    template_name = 'core/dashboard.html'
    
    def get(self, request):
        context = {}
        
        if request.user.tenant:
            # Get stats for the tenant
            context['total_locations'] = Location.objects.filter(
                tenant=request.user.tenant
            ).count()
            context['total_users'] = User.objects.filter(
                tenant=request.user.tenant
            ).count()
            context['total_shops'] = Location.objects.filter(
                tenant=request.user.tenant,
                location_type='SHOP'
            ).count()
            
            # Get locations by type
            context['locations_by_type'] = Location.objects.filter(
                tenant=request.user.tenant
            ).values('location_type').annotate(count=Count('id'))
        
        return render(request, self.template_name, context)


# Location Views
class LocationListView(LoginRequiredMixin, ListView):
    """List all locations for the tenant."""
    model = Location
    template_name = 'core/location_list.html'
    context_object_name = 'locations'
    
    def get_queryset(self):
        return Location.objects.filter(tenant=self.request.user.tenant)


class LocationCreateView(LoginRequiredMixin, CreateView):
    """Create a new location."""
    model = Location
    form_class = LocationForm
    template_name = 'core/location_form.html'
    success_url = reverse_lazy('core:location_list')
    
    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, f'Location "{form.instance.name}" created successfully!')
        return super().form_valid(form)


class LocationUpdateView(LoginRequiredMixin, UpdateView):
    """Update an existing location."""
    model = Location
    form_class = LocationForm
    template_name = 'core/location_form.html'
    success_url = reverse_lazy('core:location_list')
    
    def get_queryset(self):
        return Location.objects.filter(tenant=self.request.user.tenant)
    
    def form_valid(self, form):
        messages.success(self.request, f'Location "{form.instance.name}" updated successfully!')
        return super().form_valid(form)


class LocationDeleteView(LoginRequiredMixin, DeleteView):
    """Delete a location."""
    model = Location
    template_name = 'core/location_confirm_delete.html'
    success_url = reverse_lazy('core:location_list')
    
    def get_queryset(self):
        return Location.objects.filter(tenant=self.request.user.tenant)
    
    def form_valid(self, form):
        messages.success(self.request, 'Location deleted successfully!')
        return super().form_valid(form)


# User Views
class UserListView(LoginRequiredMixin, ListView):
    """List all users for the tenant."""
    model = User
    template_name = 'core/user_list.html'
    context_object_name = 'users'
    
    def get_queryset(self):
        return User.objects.filter(tenant=self.request.user.tenant).select_related('role', 'location')


class UserCreateView(LoginRequiredMixin, CreateView):
    """Create a new user."""
    model = User
    form_class = UserCreateForm
    template_name = 'core/user_form.html'
    success_url = reverse_lazy('core:user_list')
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        kwargs['current_user'] = self.request.user  # Pass current user for role filtering
        return kwargs
    
    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, f'User "{form.instance.email}" created successfully!')
        return super().form_valid(form)


class UserUpdateView(LoginRequiredMixin, UpdateView):
    """Update an existing user."""
    model = User
    form_class = UserEditForm
    template_name = 'core/user_form.html'
    success_url = reverse_lazy('core:user_list')
    
    def get_queryset(self):
        return User.objects.filter(tenant=self.request.user.tenant)
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        kwargs['current_user'] = self.request.user  # Pass current user for role filtering
        return kwargs
    
    def form_valid(self, form):
        messages.success(self.request, f'User "{form.instance.email}" updated successfully!')
        return super().form_valid(form)


class UserDeleteView(LoginRequiredMixin, DeleteView):
    """Delete a user."""
    model = User
    template_name = 'core/user_confirm_delete.html'
    success_url = reverse_lazy('core:user_list')
    
    def get_queryset(self):
        return User.objects.filter(tenant=self.request.user.tenant)
    
    def form_valid(self, form):
        messages.success(self.request, 'User deleted successfully!')
        return super().form_valid(form)
