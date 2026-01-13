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
from .forms import LoginForm, TenantSetupForm, LocationForm, UserCreateForm, UserEditForm, TenantSettingsForm
from .decorators import admin_required, AdminOrManagerRequiredMixin


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
        from django.db.models import Q
        from apps.transfers.models import Transfer
        
        context = {}
        user = request.user
        role_name = user.role.name if user.role else None
        
        # Redirect AUDITOR to their specialized dashboard
        if role_name == 'AUDITOR':
            return redirect('core:auditor_dashboard')
        
        # Redirect ACCOUNTANT to financial dashboard
        if role_name == 'ACCOUNTANT':
            return redirect('accounting:accountant_dashboard')
        
        # Shop attendants get limited dashboard - no location/user stats
        is_attendant = role_name == 'SHOP_ATTENDANT'
        
        if user.tenant:
            # Only show location/user stats to non-attendants
            if not is_attendant:
                context['total_locations'] = Location.objects.filter(
                    tenant=user.tenant
                ).count()
                context['total_users'] = User.objects.filter(
                    tenant=user.tenant
                ).count()
                context['total_shops'] = Location.objects.filter(
                    tenant=user.tenant,
                    location_type='SHOP'
                ).count()
                
                # Get locations by type
                context['locations_by_type'] = Location.objects.filter(
                    tenant=user.tenant
                ).values('location_type').annotate(count=Count('id'))
            
            # Get pending transfer alerts for all non-attendant users
            if not is_attendant:
                # Build location filter based on user's role/location
                role_location_map = {
                    'PRODUCTION_MANAGER': 'PRODUCTION',
                    'STORES_MANAGER': 'STORES',
                    'SHOP_MANAGER': 'SHOP',
                }
                user_location_type = role_location_map.get(role_name)
                
                # Incoming transfers awaiting receipt (SENT status, user is destination)
                incoming_filter = Q(status='SENT')
                if role_name == 'ADMIN':
                    # Admin sees all
                    pass
                elif user.location:
                    incoming_filter &= Q(destination_location=user.location)
                elif user_location_type:
                    incoming_filter &= Q(destination_location__location_type=user_location_type)
                else:
                    incoming_filter &= Q(pk__isnull=True)  # No results
                
                context['pending_incoming_transfers'] = Transfer.objects.filter(
                    tenant=user.tenant
                ).filter(incoming_filter).select_related(
                    'source_location', 'destination_location'
                ).order_by('-sent_at')[:5]
                
                # Disputed transfers requiring attention
                disputed_filter = Q(status='DISPUTED')
                if role_name == 'ADMIN':
                    pass
                elif user.location:
                    disputed_filter &= (Q(source_location=user.location) | Q(destination_location=user.location))
                elif user_location_type:
                    disputed_filter &= (Q(source_location__location_type=user_location_type) | 
                                      Q(destination_location__location_type=user_location_type))
                else:
                    disputed_filter &= Q(pk__isnull=True)
                
                context['disputed_transfers'] = Transfer.objects.filter(
                    tenant=user.tenant
                ).filter(disputed_filter).select_related(
                    'source_location', 'destination_location'
                ).order_by('-created_at')[:5]
            
            # Today's sales calculation for dashboard
            from apps.sales.models import Sale
            from django.utils import timezone
            from django.db.models import Sum
            
            today = timezone.now().date()
            sales_filter = {
                'tenant': user.tenant,
                'status': 'COMPLETED',
                'created_at__date': today
            }
            
            # For attendants, show only their own sales
            if is_attendant:
                sales_filter['attendant'] = user
            # For shop managers, show their shop's sales
            elif role_name == 'SHOP_MANAGER' and user.location:
                sales_filter['shop'] = user.location
            
            today_sales = Sale.objects.filter(**sales_filter).aggregate(
                total=Sum('total')
            )['total'] or 0
            
            context['today_sales'] = today_sales
        
        return render(request, self.template_name, context)


# Location Views
class LocationListView(LoginRequiredMixin, AdminOrManagerRequiredMixin, ListView):
    """List all locations for the tenant. Restricted to admins and managers."""
    model = Location
    template_name = 'core/location_list.html'
    context_object_name = 'locations'
    
    def get_queryset(self):
        return Location.objects.filter(tenant=self.request.user.tenant)


class LocationCreateView(LoginRequiredMixin, AdminOrManagerRequiredMixin, CreateView):
    """Create a new location. Restricted to admins and managers."""
    model = Location
    form_class = LocationForm
    template_name = 'core/location_form.html'
    success_url = reverse_lazy('core:location_list')
    
    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, f'Location "{form.instance.name}" created successfully!')
        return super().form_valid(form)


class LocationUpdateView(LoginRequiredMixin, AdminOrManagerRequiredMixin, UpdateView):
    """Update an existing location. Restricted to admins and managers."""
    model = Location
    form_class = LocationForm
    template_name = 'core/location_form.html'
    success_url = reverse_lazy('core:location_list')
    
    def get_queryset(self):
        return Location.objects.filter(tenant=self.request.user.tenant)
    
    def form_valid(self, form):
        messages.success(self.request, f'Location "{form.instance.name}" updated successfully!')
        return super().form_valid(form)


class LocationDeleteView(LoginRequiredMixin, AdminOrManagerRequiredMixin, DeleteView):
    """Delete a location. Restricted to admins and managers."""
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
        form.instance.password_reset_required = True  # Force password change on first login
        messages.success(self.request, f'User "{form.instance.email}" created successfully! They will be required to change their password on first login.')
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


class AuditorDashboardView(LoginRequiredMixin, View):
    """
    Auditor dashboard with financial reports and product movement tracking.
    Read-only view for auditors to monitor financials and inventory movements.
    """
    template_name = 'core/auditor_dashboard.html'
    
    def get(self, request):
        from django.db.models import Sum, Count, Q
        from django.utils import timezone
        from datetime import timedelta
        from apps.sales.models import Sale
        from apps.inventory.models import InventoryLedger
        from apps.transfers.models import Transfer
        
        user = request.user
        role_name = user.role.name if user.role else None
        
        # Only allow AUDITOR and ADMIN roles
        if role_name not in ['AUDITOR', 'ADMIN']:
            messages.error(request, 'You do not have permission to access the auditor dashboard.')
            return redirect('core:dashboard')
        
        context = {}
        tenant = user.tenant
        
        if not tenant:
            return render(request, self.template_name, context)
        
        # Get date range from query params
        date_range = request.GET.get('range', 'today')
        today = timezone.now().date()
        
        if date_range == 'week':
            start_date = today - timedelta(days=7)
            context['date_range_label'] = 'Last 7 Days'
        elif date_range == 'month':
            start_date = today - timedelta(days=30)
            context['date_range_label'] = 'Last 30 Days'
        elif date_range == 'all':
            start_date = None
            context['date_range_label'] = 'All Time'
        else:
            start_date = today
            context['date_range_label'] = 'Today'
        
        context['current_range'] = date_range
        
        # ============ FINANCIAL SUMMARY ============
        sales_filter = Q(tenant=tenant, status='COMPLETED')
        if start_date:
            sales_filter &= Q(created_at__date__gte=start_date)
        
        sales_data = Sale.objects.filter(sales_filter).aggregate(
            total_revenue=Sum('total'),
            total_sales=Count('id'),
            cash_sales=Sum('total', filter=Q(payment_method='CASH')),
            ecash_sales=Sum('total', filter=Q(payment_method='ECASH')),
            credit_sales=Sum('total', filter=Q(payment_method='CREDIT')),
        )
        
        context['financial'] = {
            'total_revenue': sales_data['total_revenue'] or 0,
            'total_sales': sales_data['total_sales'] or 0,
            'cash_sales': sales_data['cash_sales'] or 0,
            'ecash_sales': sales_data['ecash_sales'] or 0,
            'credit_sales': sales_data['credit_sales'] or 0,
        }
        
        # Average sale value
        if context['financial']['total_sales'] > 0:
            context['financial']['avg_sale'] = context['financial']['total_revenue'] / context['financial']['total_sales']
        else:
            context['financial']['avg_sale'] = 0
        
        # ============ PRODUCT MOVEMENT SUMMARY ============
        ledger_filter = Q(tenant=tenant)
        if start_date:
            ledger_filter &= Q(created_at__date__gte=start_date)
        
        movement_data = InventoryLedger.objects.filter(ledger_filter).values(
            'transaction_type'
        ).annotate(
            count=Count('id'),
            total_qty=Sum('quantity')
        )
        
        # Convert to dict for easy template access
        movements = {}
        for item in movement_data:
            movements[item['transaction_type']] = {
                'count': item['count'],
                'quantity': abs(item['total_qty'] or 0)
            }
        
        context['movements'] = movements
        
        # ============ RECENT LEDGER ENTRIES ============
        context['recent_ledger'] = InventoryLedger.objects.filter(
            tenant=tenant
        ).select_related(
            'product', 'location', 'created_by', 'batch'
        ).order_by('-created_at')[:15]
        
        # ============ TRANSFER SUMMARY ============
        transfer_filter = Q(tenant=tenant)
        if start_date:
            transfer_filter &= Q(created_at__date__gte=start_date)
        
        transfer_data = Transfer.objects.filter(transfer_filter).values(
            'status'
        ).annotate(count=Count('id'))
        
        transfers = {}
        for item in transfer_data:
            transfers[item['status']] = item['count']
        context['transfers'] = transfers
        
        return render(request, self.template_name, context)


class SettingsView(LoginRequiredMixin, View):
    """
    Admin settings page for tenant configuration.
    """
    template_name = 'core/settings.html'
    
    def dispatch(self, request, *args, **kwargs):
        # Only allow ADMIN role
        if not request.user.role or request.user.role.name != 'ADMIN':
            messages.error(request, 'Only administrators can access settings.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        tenant = request.user.tenant
        if not tenant:
            messages.error(request, 'No tenant associated with your account.')
            return redirect('core:dashboard')
        
        form = TenantSettingsForm(instance=tenant)
        return render(request, self.template_name, {'form': form, 'tenant': tenant})
    
    def post(self, request):
        tenant = request.user.tenant
        if not tenant:
            messages.error(request, 'No tenant associated with your account.')
            return redirect('core:dashboard')
        
        form = TenantSettingsForm(request.POST, instance=tenant)
        if form.is_valid():
            form.save()
            messages.success(request, 'Settings updated successfully!')
            return redirect('core:settings')
        
        return render(request, self.template_name, {'form': form, 'tenant': tenant})


class AdminPasswordResetView(LoginRequiredMixin, View):
    """Admin view to reset a user's password."""
    template_name = 'core/admin_password_reset.html'
    
    def dispatch(self, request, *args, **kwargs):
        # Only allow ADMIN role
        if not request.user.role or request.user.role.name != 'ADMIN':
            messages.error(request, 'Only administrators can reset passwords.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request, pk):
        from .forms import AdminPasswordResetForm
        user = get_object_or_404(User, pk=pk, tenant=request.user.tenant)
        form = AdminPasswordResetForm()
        return render(request, self.template_name, {'form': form, 'target_user': user})
    
    def post(self, request, pk):
        from .forms import AdminPasswordResetForm
        user = get_object_or_404(User, pk=pk, tenant=request.user.tenant)
        form = AdminPasswordResetForm(request.POST)
        
        if form.is_valid():
            new_password = form.cleaned_data['new_password1']
            user.set_password(new_password)
            user.password_reset_required = True  # Force password change on next login
            user.save()
            
            messages.success(request, f'Password for {user.get_full_name() or user.email} has been reset. '
                                      'They will be required to change it on their next login.')
            return redirect('core:user_list')
        
        return render(request, self.template_name, {'form': form, 'target_user': user})


class ForcedPasswordChangeView(LoginRequiredMixin, View):
    """View for users to change password on first login after admin reset."""
    template_name = 'core/forced_password_change.html'
    
    def get(self, request):
        from .forms import ForcedPasswordChangeForm
        # Only show if user needs to change password
        if not request.user.password_reset_required:
            return redirect('core:dashboard')
        
        form = ForcedPasswordChangeForm()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        from .forms import ForcedPasswordChangeForm
        if not request.user.password_reset_required:
            return redirect('core:dashboard')
        
        form = ForcedPasswordChangeForm(request.POST)
        if form.is_valid():
            new_password = form.cleaned_data['new_password1']
            request.user.set_password(new_password)
            request.user.password_reset_required = False
            request.user.save()
            
            # Update session to prevent logout
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, request.user)
            
            messages.success(request, 'Your password has been changed successfully!')
            return redirect('core:dashboard')
        
        return render(request, self.template_name, {'form': form})

