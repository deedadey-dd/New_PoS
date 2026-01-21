"""
Superadmin views for platform management.
Restricted to Django superusers only.
"""
from django.views import View
from django.views.generic import TemplateView, ListView, DetailView, UpdateView
from django.contrib.auth.mixins import UserPassesTestMixin
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count, Sum
from datetime import timedelta

from apps.core.models import Tenant, User, Location, ContactMessage


class SuperuserRequiredMixin(UserPassesTestMixin):
    """Mixin that restricts access to superusers only."""
    
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_superuser
    
    def handle_no_permission(self):
        from django.http import Http404
        raise Http404("Not Found")


class SuperadminDashboardView(SuperuserRequiredMixin, TemplateView):
    """Main superadmin dashboard showing tenant overview."""
    template_name = 'superadmin/dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        tenants = Tenant.objects.all()
        today = timezone.now().date()
        
        # Tenant statistics
        context['total_tenants'] = tenants.count()
        context['active_tenants'] = tenants.filter(
            subscription_status__in=['ACTIVE', 'TRIAL'],
            is_active=True
        ).count()
        context['expired_tenants'] = tenants.filter(subscription_status='EXPIRED').count()
        context['suspended_tenants'] = tenants.filter(subscription_status='SUSPENDED').count()
        context['trial_tenants'] = tenants.filter(subscription_status='TRIAL').count()
        
        # Expiring soon (within 30 days)
        expiring_threshold = today + timedelta(days=30)
        context['expiring_soon'] = tenants.filter(
            subscription_status__in=['ACTIVE', 'TRIAL'],
            subscription_end_date__lte=expiring_threshold,
            subscription_end_date__gte=today,
            auto_renew=False
        ).order_by('subscription_end_date')[:10]
        
        # Recently created tenants
        context['recent_tenants'] = tenants.order_by('-created_at')[:5]
        
        # User and location counts
        context['total_users'] = User.objects.filter(is_superuser=False).count()
        context['total_locations'] = Location.objects.count()
        
        # Contact messages
        context['unread_messages'] = ContactMessage.objects.filter(is_read=False).count()
        context['recent_messages'] = ContactMessage.objects.order_by('-created_at')[:5]
        
        return context


class TenantListView(SuperuserRequiredMixin, ListView):
    """List all tenants with filtering."""
    model = Tenant
    template_name = 'superadmin/tenant_list.html'
    context_object_name = 'tenants'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = Tenant.objects.annotate(
            user_count=Count('users', distinct=True),
            location_count=Count('location_set', distinct=True)
        )
        
        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(subscription_status=status)
        
        # Filter by active state
        active = self.request.GET.get('active')
        if active == '1':
            queryset = queryset.filter(is_active=True)
        elif active == '0':
            queryset = queryset.filter(is_active=False)
        
        # Search
        search = self.request.GET.get('q')
        if search:
            queryset = queryset.filter(name__icontains=search)
        
        return queryset.order_by('name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['status_choices'] = Tenant.SUBSCRIPTION_STATUS_CHOICES
        context['current_status'] = self.request.GET.get('status', '')
        context['current_active'] = self.request.GET.get('active', '')
        context['search_query'] = self.request.GET.get('q', '')
        return context


class TenantDetailView(SuperuserRequiredMixin, DetailView):
    """View tenant details."""
    model = Tenant
    template_name = 'superadmin/tenant_detail.html'
    context_object_name = 'tenant'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.object
        
        # Get tenant stats
        context['users'] = User.objects.filter(tenant=tenant)
        context['locations'] = Location.objects.filter(tenant=tenant)
        context['user_count'] = context['users'].count()
        context['location_count'] = context['locations'].count()
        
        return context


class TenantActivateView(SuperuserRequiredMixin, View):
    """Activate a tenant."""
    
    def post(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        tenant.is_active = True
        tenant.subscription_status = 'ACTIVE'
        tenant.save()
        messages.success(request, f"Tenant '{tenant.name}' has been activated.")
        return redirect('superadmin:tenant_detail', pk=pk)


class TenantDeactivateView(SuperuserRequiredMixin, View):
    """Deactivate a tenant."""
    
    def post(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        tenant.is_active = False
        tenant.subscription_status = 'SUSPENDED'
        tenant.save()
        messages.warning(request, f"Tenant '{tenant.name}' has been deactivated.")
        return redirect('superadmin:tenant_detail', pk=pk)


class TenantSubscriptionView(SuperuserRequiredMixin, View):
    """Update tenant subscription."""
    template_name = 'superadmin/tenant_subscription_form.html'
    
    def get(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        return render(request, self.template_name, {'tenant': tenant})
    
    def post(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        
        # Update subscription fields
        status = request.POST.get('subscription_status')
        if status in dict(Tenant.SUBSCRIPTION_STATUS_CHOICES):
            tenant.subscription_status = status
        
        start_date = request.POST.get('subscription_start_date')
        if start_date:
            from datetime import datetime
            tenant.subscription_start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        
        end_date = request.POST.get('subscription_end_date')
        if end_date:
            from datetime import datetime
            tenant.subscription_end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        
        tenant.auto_renew = request.POST.get('auto_renew') == 'on'
        tenant.admin_notes = request.POST.get('admin_notes', '')
        
        # Update is_active based on status
        tenant.is_active = status not in ['EXPIRED', 'SUSPENDED']
        
        tenant.save()
        messages.success(request, f"Subscription updated for '{tenant.name}'.")
        return redirect('superadmin:tenant_detail', pk=pk)


class TenantExtendView(SuperuserRequiredMixin, View):
    """Quick action to extend tenant subscription by 1 year."""
    
    def post(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        
        # Extend by 1 year from current end date or today
        if tenant.subscription_end_date and tenant.subscription_end_date > timezone.now().date():
            tenant.subscription_end_date = tenant.subscription_end_date + timedelta(days=365)
        else:
            tenant.subscription_end_date = timezone.now().date() + timedelta(days=365)
        
        tenant.subscription_status = 'ACTIVE'
        tenant.is_active = True
        tenant.save()
        
        messages.success(
            request, 
            f"Subscription extended to {tenant.subscription_end_date.strftime('%B %d, %Y')}."
        )
        return redirect('superadmin:tenant_detail', pk=pk)


class ContactMessageListView(SuperuserRequiredMixin, ListView):
    """List all contact form submissions."""
    model = ContactMessage
    template_name = 'superadmin/contact_messages.html'
    context_object_name = 'messages_list'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = ContactMessage.objects.all()
        
        # Filter by read status
        status = self.request.GET.get('status')
        if status == 'unread':
            queryset = queryset.filter(is_read=False)
        elif status == 'read':
            queryset = queryset.filter(is_read=True)
        
        # Search
        search = self.request.GET.get('q')
        if search:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(phone__icontains=search) |
                Q(email__icontains=search)
            )
        
        return queryset.order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('q', '')
        context['current_status'] = self.request.GET.get('status', '')
        context['unread_count'] = ContactMessage.objects.filter(is_read=False).count()
        return context


class ContactMessageDetailView(SuperuserRequiredMixin, DetailView):
    """View contact message details and mark as read."""
    model = ContactMessage
    template_name = 'superadmin/contact_message_detail.html'
    context_object_name = 'contact'
    
    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        # Mark as read when viewed
        obj.mark_as_read()
        return obj


class ContactMessageUpdateNotesView(SuperuserRequiredMixin, View):
    """Update notes on a contact message."""
    
    def post(self, request, pk):
        contact = get_object_or_404(ContactMessage, pk=pk)
        contact.notes = request.POST.get('notes', '')
        contact.save(update_fields=['notes'])
        messages.success(request, "Notes updated.")
        return redirect('superadmin:contact_detail', pk=pk)


class ContactMessageDeleteView(SuperuserRequiredMixin, View):
    """Delete a contact message."""
    
    def post(self, request, pk):
        contact = get_object_or_404(ContactMessage, pk=pk)
        contact.delete()
        messages.success(request, "Message deleted.")
        return redirect('superadmin:contact_list')


class ContactMessageMarkReadView(SuperuserRequiredMixin, View):
    """Mark a message as read/unread."""
    
    def post(self, request, pk):
        contact = get_object_or_404(ContactMessage, pk=pk)
        action = request.POST.get('action', 'read')
        
        if action == 'unread':
            contact.is_read = False
            contact.read_at = None
            contact.save(update_fields=['is_read', 'read_at'])
            messages.info(request, "Marked as unread.")
        else:
            contact.mark_as_read()
            messages.info(request, "Marked as read.")
        
        return redirect('superadmin:contact_list')


# ============== TENANT MANAGER VIEWS ==============

class TenantManagerListView(SuperuserRequiredMixin, ListView):
    """List all Tenant Managers."""
    template_name = 'superadmin/tenant_manager_list.html'
    context_object_name = 'managers'
    
    def get_queryset(self):
        from apps.core.models import Role
        tenant_manager_role = Role.objects.filter(name='TENANT_MANAGER').first()
        if tenant_manager_role:
            return User.objects.filter(
                role=tenant_manager_role,
                is_superuser=False
            ).prefetch_related('managed_tenants')
        return User.objects.none()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_managers'] = self.get_queryset().count()
        return context


class TenantManagerCreateView(SuperuserRequiredMixin, View):
    """Create a new Tenant Manager."""
    template_name = 'superadmin/tenant_manager_form.html'
    
    def get(self, request):
        return render(request, self.template_name, {
            'tenants': Tenant.objects.filter(is_active=True).order_by('name'),
        })
    
    def post(self, request):
        from apps.core.models import Role
        
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        password = request.POST.get('password', '')
        assigned_tenants = request.POST.getlist('assigned_tenants')
        
        # Validation
        errors = []
        if not email:
            errors.append("Email is required.")
        if not password:
            errors.append("Password is required.")
        if User.objects.filter(email=email).exists():
            errors.append("A user with this email already exists.")
        
        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, self.template_name, {
                'tenants': Tenant.objects.filter(is_active=True).order_by('name'),
            })
        
        # Get or create TENANT_MANAGER role
        tenant_manager_role, _ = Role.objects.get_or_create(
            name='TENANT_MANAGER',
            defaults={'description': 'Manages subscriptions for assigned tenants'}
        )
        
        # Create user
        user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            role=tenant_manager_role,
            is_active=True,
            tenant=None  # Tenant managers don't belong to a tenant
        )
        
        # Assign tenants
        from apps.subscriptions.models import TenantManagerAssignment
        for tenant_id in assigned_tenants:
            try:
                tenant = Tenant.objects.get(pk=tenant_id)
                TenantManagerAssignment.objects.create(
                    manager=user,
                    tenant=tenant,
                    is_primary=len(assigned_tenants) == 1
                )
            except Tenant.DoesNotExist:
                pass
        
        messages.success(request, f"Tenant Manager '{user.get_full_name() or user.email}' created successfully.")
        return redirect('superadmin:tenant_manager_list')


class TenantManagerDetailView(SuperuserRequiredMixin, DetailView):
    """View Tenant Manager details."""
    model = User
    template_name = 'superadmin/tenant_manager_detail.html'
    context_object_name = 'manager'
    
    def get_queryset(self):
        from apps.core.models import Role
        tenant_manager_role = Role.objects.filter(name='TENANT_MANAGER').first()
        if tenant_manager_role:
            return User.objects.filter(role=tenant_manager_role, is_superuser=False)
        return User.objects.none()
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.subscriptions.models import TenantManagerAssignment
        context['assignments'] = TenantManagerAssignment.objects.filter(
            manager=self.object
        ).select_related('tenant')
        return context


class TenantManagerEditView(SuperuserRequiredMixin, View):
    """Edit a Tenant Manager."""
    template_name = 'superadmin/tenant_manager_form.html'
    
    def get_manager(self, pk):
        from apps.core.models import Role
        tenant_manager_role = Role.objects.filter(name='TENANT_MANAGER').first()
        if tenant_manager_role:
            return get_object_or_404(User, pk=pk, role=tenant_manager_role)
        raise Http404()
    
    def get(self, request, pk):
        from apps.subscriptions.models import TenantManagerAssignment
        manager = self.get_manager(pk)
        assigned_ids = TenantManagerAssignment.objects.filter(
            manager=manager
        ).values_list('tenant_id', flat=True)
        
        return render(request, self.template_name, {
            'manager': manager,
            'tenants': Tenant.objects.filter(is_active=True).order_by('name'),
            'assigned_tenant_ids': list(assigned_ids),
            'is_edit': True,
        })
    
    def post(self, request, pk):
        from apps.subscriptions.models import TenantManagerAssignment
        manager = self.get_manager(pk)
        
        manager.first_name = request.POST.get('first_name', '').strip()
        manager.last_name = request.POST.get('last_name', '').strip()
        manager.phone = request.POST.get('phone', '').strip()
        manager.is_active = request.POST.get('is_active') == 'on'
        
        # Update password if provided
        new_password = request.POST.get('password', '')
        if new_password:
            manager.set_password(new_password)
        
        manager.save()
        
        # Update tenant assignments
        assigned_tenants = request.POST.getlist('assigned_tenants')
        TenantManagerAssignment.objects.filter(manager=manager).delete()
        
        for tenant_id in assigned_tenants:
            try:
                tenant = Tenant.objects.get(pk=tenant_id)
                TenantManagerAssignment.objects.create(
                    manager=manager,
                    tenant=tenant,
                    is_primary=len(assigned_tenants) == 1
                )
            except Tenant.DoesNotExist:
                pass
        
        messages.success(request, f"Tenant Manager '{manager.get_full_name() or manager.email}' updated.")
        return redirect('superadmin:tenant_manager_detail', pk=pk)


class TenantManagerDeleteView(SuperuserRequiredMixin, View):
    """Delete a Tenant Manager."""
    
    def post(self, request, pk):
        from apps.core.models import Role
        tenant_manager_role = Role.objects.filter(name='TENANT_MANAGER').first()
        if tenant_manager_role:
            manager = get_object_or_404(User, pk=pk, role=tenant_manager_role)
            name = manager.get_full_name() or manager.email
            manager.delete()
            messages.success(request, f"Tenant Manager '{name}' deleted.")
        return redirect('superadmin:tenant_manager_list')


# ============== TENANT UNLOCK VIEW ==============

class TenantUnlockView(SuperuserRequiredMixin, View):
    """Unlock a locked tenant account."""
    
    def post(self, request, pk):
        tenant = get_object_or_404(Tenant, pk=pk)
        
        if tenant.subscription_status != 'LOCKED' and tenant.locked_at is None:
            messages.warning(request, f"Tenant '{tenant.name}' is not locked.")
            return redirect('superadmin:tenant_detail', pk=pk)
        
        # Unlock the account
        tenant.subscription_status = 'INACTIVE'  # Set to inactive, needs to renew
        tenant.locked_at = None
        tenant.is_active = True
        tenant.admin_notes += f"\n[{timezone.now().strftime('%Y-%m-%d %H:%M')}] Account unlocked by {request.user.email}"
        tenant.save()
        
        messages.success(request, f"Tenant '{tenant.name}' has been unlocked. They should renew their subscription.")
        return redirect('superadmin:tenant_detail', pk=pk)


# ============== SUBSCRIPTION PAYMENT VIEWS ==============

class TenantPaymentListView(SuperuserRequiredMixin, ListView):
    """List all subscription payments for a tenant."""
    template_name = 'superadmin/tenant_payments.html'
    context_object_name = 'payments'
    paginate_by = 20
    
    def get_queryset(self):
        from apps.subscriptions.models import SubscriptionPayment
        self.tenant = get_object_or_404(Tenant, pk=self.kwargs['pk'])
        return SubscriptionPayment.objects.filter(tenant=self.tenant).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tenant'] = self.tenant
        return context


class RecordPaymentView(SuperuserRequiredMixin, View):
    """Record a manual subscription payment."""
    template_name = 'superadmin/record_payment.html'
    
    def get(self, request, pk):
        from apps.subscriptions.models import SubscriptionPlan
        tenant = get_object_or_404(Tenant, pk=pk)
        plans = SubscriptionPlan.objects.filter(is_active=True)
        return render(request, self.template_name, {
            'tenant': tenant,
            'plans': plans,
        })
    
    def post(self, request, pk):
        from apps.subscriptions.models import SubscriptionPayment, SubscriptionPlan
        from decimal import Decimal
        
        tenant = get_object_or_404(Tenant, pk=pk)
        
        payment_type = request.POST.get('payment_type')
        amount = request.POST.get('amount')
        payment_method = request.POST.get('payment_method')
        reference = request.POST.get('reference', '')
        months = int(request.POST.get('months', 1))
        
        try:
            amount = Decimal(amount)
        except:
            messages.error(request, "Invalid amount.")
            return redirect('superadmin:record_payment', pk=pk)
        
        # Create payment record
        payment = SubscriptionPayment.objects.create(
            tenant=tenant,
            payment_type=payment_type,
            amount=amount,
            currency=tenant.currency,
            status='COMPLETED',
            payment_method=payment_method,
            transaction_reference=reference,
            plan_name=tenant.subscription_plan.name if tenant.subscription_plan else None,
            recorded_by=request.user,
            notes=f"Manually recorded by {request.user.email}"
        )
        
        # Update subscription dates
        if payment_type == 'ONBOARDING':
            tenant.onboarding_paid = True
            tenant.save(update_fields=['onboarding_paid'])
        elif payment_type in ['SUBSCRIPTION', 'RENEWAL']:
            # Extend subscription
            today = timezone.now().date()
            if tenant.subscription_end_date and tenant.subscription_end_date > today:
                tenant.subscription_end_date = tenant.subscription_end_date + timedelta(days=30 * months)
            else:
                tenant.subscription_start_date = today
                tenant.subscription_end_date = today + timedelta(days=30 * months)
            
            tenant.subscription_status = 'ACTIVE'
            tenant.is_active = True
            tenant.save()
            
            # Update payment period
            payment.period_start = tenant.subscription_start_date
            payment.period_end = tenant.subscription_end_date
            payment.save()
        
        messages.success(request, f"Payment of {tenant.currency_symbol}{amount} recorded successfully.")
        return redirect('superadmin:tenant_payments', pk=pk)
