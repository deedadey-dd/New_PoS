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
from django.http import JsonResponse
from django.core.mail import send_mail
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.conf import settings

from .models import Tenant, Location, Role, User
from .forms import LoginForm, TenantSetupForm, LocationForm, UserCreateForm, UserEditForm, TenantSettingsForm
from .decorators import admin_required, AdminOrManagerRequiredMixin, AdminRequiredMixin


class HomePageView(View):
    """Display the home/landing page with features and login form."""
    template_name = 'core/home.html'
    
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('core:dashboard')
        form = LoginForm()
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        """Handle login form submission from home page."""
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


class LocationUpdateView(LoginRequiredMixin, AdminRequiredMixin, UpdateView):
    """Update an existing location. Restricted to admins only."""
    model = Location
    form_class = LocationForm
    template_name = 'core/location_form.html'
    success_url = reverse_lazy('core:location_list')
    
    def get_queryset(self):
        return Location.objects.filter(tenant=self.request.user.tenant)
    
    def form_valid(self, form):
        messages.success(self.request, f'Location "{form.instance.name}" updated successfully!')
        return super().form_valid(form)


class LocationDeleteView(LoginRequiredMixin, AdminRequiredMixin, DeleteView):
    """Delete a location. Restricted to admins only."""
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
    Auditor dashboard with financial reports, product movement tracking,
    cash transfers, e-cash transactions, shifts, and comprehensive sales reporting.
    """
    template_name = 'core/auditor_dashboard.html'
    
    def get(self, request):
        from django.db.models import Sum, Count, Q, F
        from django.utils import timezone
        from datetime import timedelta, datetime
        from apps.sales.models import Sale, SaleItem, Shift
        from apps.inventory.models import InventoryLedger
        from apps.transfers.models import Transfer
        from apps.accounting.models import CashTransfer
        from apps.payments.models import ECashLedger
        from apps.customers.models import Customer
        
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
        
        today = timezone.now().date()
        
        # ============ DATE RANGE HANDLING ============
        # Support both preset ranges and custom dates
        date_range = request.GET.get('range', 'today')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        
        # Custom date range takes priority
        if date_from and date_to:
            try:
                start_date = datetime.strptime(date_from, '%Y-%m-%d').date()
                end_date = datetime.strptime(date_to, '%Y-%m-%d').date()
                context['date_range_label'] = f'{start_date.strftime("%b %d")} - {end_date.strftime("%b %d, %Y")}'
                context['current_range'] = 'custom'
                context['date_from'] = start_date
                context['date_to'] = end_date
            except ValueError:
                start_date = today
                end_date = today
                context['date_range_label'] = 'Today'
                context['current_range'] = 'today'
        else:
            end_date = today
            if date_range == 'week':
                start_date = today - timedelta(days=7)
                context['date_range_label'] = 'Last 7 Days'
            elif date_range == 'month':
                start_date = today - timedelta(days=30)
                context['date_range_label'] = 'Last 30 Days'
            elif date_range == 'all':
                start_date = None
                end_date = None
                context['date_range_label'] = 'All Time'
            else:
                start_date = today
                context['date_range_label'] = 'Today'
            context['current_range'] = date_range
        
        # Build date filter for queries
        def get_date_filter(field_name='created_at__date'):
            q = Q()
            if start_date:
                q &= Q(**{f'{field_name}__gte': start_date})
            if end_date:
                q &= Q(**{f'{field_name}__lte': end_date})
            return q
        
        # ============ FINANCIAL SUMMARY ============
        sales_filter = Q(tenant=tenant, status='COMPLETED') & get_date_filter()
        
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
        ledger_filter = Q(tenant=tenant) & get_date_filter()
        
        movement_data = InventoryLedger.objects.filter(ledger_filter).values(
            'transaction_type'
        ).annotate(
            count=Count('id'),
            total_qty=Sum('quantity')
        )
        
        movements = {}
        for item in movement_data:
            movements[item['transaction_type']] = {
                'count': item['count'],
                'quantity': abs(item['total_qty'] or 0)
            }
        context['movements'] = movements
        
        # ============ RECENT LEDGER ENTRIES ============
        recent_ledger_filter = Q(tenant=tenant) & get_date_filter()
        context['recent_ledger'] = InventoryLedger.objects.filter(
            recent_ledger_filter
        ).select_related(
            'product', 'location', 'created_by', 'batch'
        ).order_by('-created_at')[:20]
        
        # ============ TRANSFER SUMMARY ============
        transfer_filter = Q(tenant=tenant) & get_date_filter()
        
        transfer_data = Transfer.objects.filter(transfer_filter).values(
            'status'
        ).annotate(count=Count('id'))
        
        transfers = {}
        for item in transfer_data:
            transfers[item['status']] = item['count']
        context['transfers'] = transfers
        
        # ============ CASH TRANSFERS ============
        cash_transfer_filter = Q(tenant=tenant) & get_date_filter()
        context['cash_transfers'] = CashTransfer.objects.filter(
            cash_transfer_filter
        ).select_related(
            'from_user', 'to_user', 'from_location', 'to_location'
        ).order_by('-created_at')[:20]
        
        # Cash transfer summary
        cash_transfer_summary = CashTransfer.objects.filter(
            cash_transfer_filter
        ).aggregate(
            total_deposits=Sum('amount', filter=Q(transfer_type='DEPOSIT', status='CONFIRMED')),
            total_floats=Sum('amount', filter=Q(transfer_type='FLOAT', status='CONFIRMED')),
            pending_count=Count('id', filter=Q(status='PENDING'))
        )
        context['cash_transfer_summary'] = cash_transfer_summary
        
        # ============ E-CASH TRANSACTIONS ============
        ecash_filter = Q(tenant=tenant) & get_date_filter()
        context['ecash_ledger'] = ECashLedger.objects.filter(
            ecash_filter
        ).select_related('created_by', 'shop').order_by('-created_at')[:20]
        
        # E-cash summary
        ecash_summary = ECashLedger.objects.filter(ecash_filter).aggregate(
            total_payments=Sum('amount', filter=Q(transaction_type='PAYMENT')),
            total_withdrawals=Sum('amount', filter=Q(transaction_type='WITHDRAWAL'))
        )
        context['ecash_summary'] = ecash_summary
        
        # ============ SHIFT HISTORY ============
        shift_filter = Q(shop__tenant=tenant) & get_date_filter('start_time__date')
        context['shifts'] = Shift.objects.filter(
            shift_filter
        ).select_related('shop', 'attendant').order_by('-start_time')[:20]
        
        # Shift summary
        shift_summary = Shift.objects.filter(shift_filter).aggregate(
            total_shifts=Count('id'),
            open_shifts=Count('id', filter=Q(status='OPEN')),
            closed_shifts=Count('id', filter=Q(status='CLOSED'))
        )
        context['shift_summary'] = shift_summary
        
        # ============ SALES BY DAY ============
        context['sales_by_day'] = Sale.objects.filter(sales_filter).values(
            'created_at__date'
        ).annotate(
            count=Count('id'),
            revenue=Sum('total')
        ).order_by('-created_at__date')[:30]
        
        # ============ SALES BY SHOP ============
        context['sales_by_shop'] = Sale.objects.filter(sales_filter).values(
            'shop__name'
        ).annotate(
            count=Count('id'),
            revenue=Sum('total')
        ).order_by('-revenue')
        
        # ============ SALES BY ATTENDANT ============
        context['sales_by_attendant'] = Sale.objects.filter(sales_filter).values(
            'attendant__first_name', 'attendant__last_name', 'attendant__email'
        ).annotate(
            count=Count('id'),
            revenue=Sum('total')
        ).order_by('-revenue')
        
        # ============ TOP PRODUCTS ============
        product_sales_filter = Q(sale__tenant=tenant, sale__status='COMPLETED') & get_date_filter('sale__created_at__date')
        context['top_products'] = SaleItem.objects.filter(
            product_sales_filter
        ).values('product__name').annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total')
        ).order_by('-revenue')[:10]
        
        # ============ ALL PRODUCTS BREAKDOWN ============
        all_products = SaleItem.objects.filter(
            product_sales_filter
        ).values('product__name').annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total')
        ).order_by('product__name')
        context['all_products'] = all_products
        
        # Calculate totals for footer
        context['all_products_total_qty'] = sum(p['qty_sold'] or 0 for p in all_products)
        context['all_products_total_revenue'] = sum(p['revenue'] or 0 for p in all_products)
        
        # ============ CREDIT SALES SUMMARY ============
        context['total_credit_outstanding'] = Customer.objects.filter(
            tenant=tenant
        ).aggregate(total=Sum('current_balance'))['total'] or 0
        
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


class HelpView(View):
    """Display help and documentation page. Accessible without login."""
    template_name = 'core/help.html'
    
    def get(self, request):
        return render(request, self.template_name)


class DocumentationView(View):
    """Display full documentation/user manual page. Accessible without login."""
    template_name = 'core/documentation.html'
    
    def get(self, request):
        return render(request, self.template_name)


class StartupKitView(View):
    """Display the Startup Kit / Hardware selection page for potential clients."""
    template_name = 'core/startup_kit.html'
    
    def get(self, request):
        return render(request, self.template_name)


@method_decorator(csrf_exempt, name='dispatch')
class ContactSubmitView(View):
    """Handle contact form submission from home page."""
    
    def post(self, request):
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            name = request.POST.get('name', '').strip()
            phone = request.POST.get('phone', '').strip()
            email = request.POST.get('email', '').strip()
            message = request.POST.get('message', '').strip()
            whatsapp_contact = request.POST.get('whatsapp_contact') == 'on'
            
            # Validate required fields
            if not name or not phone:
                return JsonResponse({
                    'success': False, 
                    'error': 'Name and phone number are required.'
                }, status=400)
            
            # Import here to avoid circular imports
            from .models import ContactMessage
            from .notifications import notify_new_contact
            
            # Save to database
            contact_msg = ContactMessage.objects.create(
                name=name,
                phone=phone,
                email=email,
                message=message,
                whatsapp_contact=whatsapp_contact
            )
            
            # Send Telegram notification (runs in background, doesn't block)
            try:
                notify_new_contact(contact_msg)
            except Exception as e:
                logger.error(f"Failed to send Telegram notification: {e}")
                # Don't fail the request if notification fails
            
            # Return success immediately (email disabled for now to avoid blocking)
            logger.info(f"Contact form submitted successfully for {name}")
            return JsonResponse({'success': True}, content_type='application/json')
            
        except Exception as e:
            import traceback
            logger.error(f"Error processing contact form: {e}")
            logger.error(traceback.format_exc())
            return JsonResponse({
                'success': False, 
                'error': 'An error occurred. Please try again.'
            }, status=500, content_type='application/json')

