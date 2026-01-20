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

