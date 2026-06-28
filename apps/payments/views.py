"""
Views for the payments app.
Handles payment provider configuration, e-cash withdrawals, and webhooks.
"""
import json
import logging
from decimal import Decimal

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, TemplateView
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from django.utils import timezone

from django.urls import reverse_lazy
from .models import PaymentProviderConfig, ShopPaymentAssignment, ECashLedger, ECashWithdrawal
from .forms import PaymentProviderConfigForm, ShopPaymentAssignmentForm
from apps.core.mixins import SortableMixin

class PaymentProviderConfigListView(LoginRequiredMixin, SortableMixin, ListView):
    """List payment provider configurations."""
    model = PaymentProviderConfig
    template_name = 'payments/config_list.html'
    context_object_name = 'configs'
    paginate_by = 25
    
    sortable_fields = ['provider', 'nickname', 'is_active', 'test_mode', 'created_at']
    default_sort = '-created_at'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role and request.user.role.name != 'ADMIN':
            messages.error(request, "Only administrators can access payment settings.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = PaymentProviderConfig.objects.filter(tenant=self.request.user.tenant)
        
        q = self.request.GET.get('q', '').strip()
        if q:
            queryset = queryset.filter(nickname__icontains=q)
            
        provider = self.request.GET.get('provider')
        if provider:
            queryset = queryset.filter(provider=provider)
            
        status = self.request.GET.get('status')
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)
            
        return self.apply_sorting(queryset)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ecash_balance'] = ECashLedger.get_current_balance(self.request.user.tenant)
        context['provider_choices'] = PaymentProviderConfig.PROVIDER_CHOICES
        return context

class PaymentProviderConfigCreateView(LoginRequiredMixin, CreateView):
    """Create a new payment provider configuration."""
    model = PaymentProviderConfig
    form_class = PaymentProviderConfigForm
    template_name = 'payments/config_form.html'
    success_url = reverse_lazy('payments:provider_settings')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role and request.user.role.name != 'ADMIN':
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, "Payment provider config created successfully!")
        return super().form_valid(form)

class PaymentProviderConfigUpdateView(LoginRequiredMixin, UpdateView):
    """Update an existing payment provider configuration."""
    model = PaymentProviderConfig
    form_class = PaymentProviderConfigForm
    template_name = 'payments/config_form.html'
    success_url = reverse_lazy('payments:provider_settings')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role and request.user.role.name != 'ADMIN':
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return PaymentProviderConfig.objects.filter(tenant=self.request.user.tenant)

    def form_valid(self, form):
        messages.success(self.request, "Payment provider config updated successfully!")
        return super().form_valid(form)

class ShopPaymentAssignmentListView(LoginRequiredMixin, SortableMixin, ListView):
    """List shop payment assignments."""
    model = ShopPaymentAssignment
    template_name = 'payments/assignment_list.html'
    context_object_name = 'assignments'
    paginate_by = 25
    
    sortable_fields = ['shop__name', 'provider_config__provider', 'is_default', 'priority']
    default_sort = 'shop__name'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role and request.user.role.name != 'ADMIN':
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = ShopPaymentAssignment.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('shop', 'provider_config')
        
        shop_id = self.request.GET.get('shop')
        if shop_id:
            queryset = queryset.filter(shop_id=shop_id)
            
        provider_id = self.request.GET.get('provider')
        if provider_id:
            queryset = queryset.filter(provider_config_id=provider_id)
            
        return self.apply_sorting(queryset)
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.core.models import Location
        context['shops'] = Location.objects.filter(tenant=self.request.user.tenant, location_type='SHOP', is_active=True)
        context['providers'] = PaymentProviderConfig.objects.filter(tenant=self.request.user.tenant)
        return context

class ShopPaymentAssignmentCreateView(LoginRequiredMixin, CreateView):
    """Assign a payment provider to a shop."""
    model = ShopPaymentAssignment
    form_class = ShopPaymentAssignmentForm
    template_name = 'payments/assignment_form.html'
    success_url = reverse_lazy('payments:shop_assignments')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role and request.user.role.name != 'ADMIN':
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        return kwargs

    def form_valid(self, form):
        form.instance.tenant = self.request.user.tenant
        messages.success(self.request, "Shop assignment created successfully!")
        return super().form_valid(form)

class ShopPaymentAssignmentUpdateView(LoginRequiredMixin, UpdateView):
    """Update a shop payment assignment."""
    model = ShopPaymentAssignment
    form_class = ShopPaymentAssignmentForm
    template_name = 'payments/assignment_form.html'
    success_url = reverse_lazy('payments:shop_assignments')

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if request.user.role and request.user.role.name != 'ADMIN':
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return ShopPaymentAssignment.objects.filter(tenant=self.request.user.tenant)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tenant'] = self.request.user.tenant
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Shop assignment updated successfully!")
        return super().form_valid(form)


@login_required
@require_POST
def test_connection(request):
    """Test connection to payment provider."""
    tenant = request.user.tenant
    
    try:
        provider_config_id = request.POST.get('config_id')
        if provider_config_id:
            provider = get_payment_provider(tenant, config_id=provider_config_id)
        else:
            provider = get_payment_provider(tenant)
            
        if not provider:
            return JsonResponse({
                'success': False,
                'message': 'Payment provider not configured or inactive.'
            })
        
        result = provider.test_connection()
        return JsonResponse(result.to_dict())
    
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error: {str(e)}'
        })


class ECashWithdrawalListView(LoginRequiredMixin, ListView):
    """List of e-cash withdrawals."""
    model = ECashWithdrawal
    template_name = 'payments/withdrawal_list.html'
    context_object_name = 'withdrawals'
    paginate_by = 20
    
    def get_queryset(self):
        return ECashWithdrawal.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('withdrawn_by').order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ecash_balance'] = ECashLedger.get_current_balance(self.request.user.tenant)
        return context


class ECashWithdrawalCreateView(LoginRequiredMixin, CreateView):
    """Create a new e-cash withdrawal."""
    model = ECashWithdrawal
    template_name = 'payments/withdrawal_form.html'
    fields = ['amount', 'notes']
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        # Only Accountant and Admin can withdraw
        if request.user.role and request.user.role.name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, "Only accountants can withdraw e-cash.")
            return redirect('payments:withdrawal_list')
        return super().dispatch(request, *args, **kwargs)
    
    def form_valid(self, form):
        tenant = self.request.user.tenant
        amount = form.cleaned_data['amount']
        
        # Check balance
        balance = ECashLedger.get_current_balance(tenant)
        if amount > balance:
            messages.error(self.request, f"Insufficient e-cash balance. Available: {balance}")
            return self.form_invalid(form)
        
        # Create withdrawal
        withdrawal = form.save(commit=False)
        withdrawal.tenant = tenant
        withdrawal.withdrawn_by = self.request.user
        withdrawal.save()
        
        # Complete immediately (auto-confirm for accountant)
        try:
            withdrawal.complete(self.request.user)
            messages.success(
                self.request, 
                f"Withdrawal of {amount} completed. Please add this to your physical cash count."
            )
        except Exception as e:
            messages.error(self.request, f"Withdrawal failed: {str(e)}")
        
        return redirect('payments:withdrawal_list')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ecash_balance'] = ECashLedger.get_current_balance(self.request.user.tenant)
        return context


@login_required
@require_POST
def complete_withdrawal(request, pk):
    """Complete a pending withdrawal."""
    withdrawal = get_object_or_404(
        ECashWithdrawal,
        pk=pk,
        tenant=request.user.tenant
    )
    
    try:
        withdrawal.complete(request.user)
        messages.success(request, f"Withdrawal {withdrawal.withdrawal_number} completed!")
    except Exception as e:
        messages.error(request, str(e))
    
    return redirect('payments:withdrawal_list')


@login_required
@require_POST
def cancel_withdrawal(request, pk):
    """Cancel a pending withdrawal."""
    withdrawal = get_object_or_404(
        ECashWithdrawal,
        pk=pk,
        tenant=request.user.tenant
    )
    
    reason = request.POST.get('reason', '')
    
    try:
        withdrawal.cancel(reason)
        messages.success(request, f"Withdrawal {withdrawal.withdrawal_number} cancelled.")
    except Exception as e:
        messages.error(request, str(e))
    
    return redirect('payments:withdrawal_list')


class ECashLedgerView(LoginRequiredMixin, SortableMixin, ListView):
    """E-Cash transaction history."""
    model = ECashLedger
    template_name = 'payments/ecash_ledger.html'
    context_object_name = 'transactions'
    paginate_by = 50
    sortable_fields = ['created_at', 'amount', 'transaction_type', 'reference', 'shop__name']
    default_sort = '-created_at'
    
    def get_queryset(self):
        from datetime import datetime
        
        queryset = ECashLedger.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('created_by', 'shop')
        
        # Apply filters
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        min_amount = self.request.GET.get('min_amount')
        max_amount = self.request.GET.get('max_amount')
        shop_id = self.request.GET.get('shop')
        provider_config_id = self.request.GET.get('provider_config')
        
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass
                
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass
                
        if min_amount:
            try:
                queryset = queryset.filter(amount__gte=Decimal(min_amount))
            except (ValueError, TypeError):
                pass
                
        if max_amount:
            try:
                queryset = queryset.filter(amount__lte=Decimal(max_amount))
            except (ValueError, TypeError):
                pass
                
        if shop_id:
            queryset = queryset.filter(shop_id=shop_id)
            
        if provider_config_id:
            queryset = queryset.filter(provider_config_id=provider_config_id)
            
        return self.apply_sorting(queryset)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['shop_ecash_balance'] = ECashLedger.get_current_balance(self.request.user.tenant)
        
        from apps.core.models import Location
        from apps.payments.models import PaymentProviderConfig
        context['shops'] = Location.objects.filter(tenant=self.request.user.tenant, location_type='SHOP')
        context['providers'] = PaymentProviderConfig.objects.filter(tenant=self.request.user.tenant, is_active=True)
        
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['min_amount'] = self.request.GET.get('min_amount', '')
        context['max_amount'] = self.request.GET.get('max_amount', '')
        context['selected_shop'] = self.request.GET.get('shop', '')
        context['selected_provider'] = self.request.GET.get('provider_config', '')
        return context


class ShopECashListView(LoginRequiredMixin, TemplateView):
    """
    Accountant view: List all shops with their E-Cash balances.
    Allows withdrawing e-cash from specific shops.
    """
    template_name = 'payments/shop_ecash_list.html'
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        # Only Accountant and Admin can access
        if request.user.role and request.user.role.name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, "Only accountants can access shop e-cash balances.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.request.user.tenant
        
        # Get all shops
        from apps.core.models import Location
        shops = Location.objects.filter(
            tenant=tenant,
            location_type='SHOP',
            is_active=True
        ).order_by('name')
        
        # Calculate e-cash balance for each shop
        shop_balances = []
        total_ecash = Decimal('0')
        from django.db.models import Sum
        
        for shop in shops:
            balance = ECashLedger.get_shop_balance(tenant, shop)
            
            # Fetch breakdown by platform
            provider_balances = []
            qs = ECashLedger.objects.filter(tenant=tenant, shop=shop)
            platform_sums = qs.values('provider_config', 'provider_config__nickname', 'provider').annotate(total=Sum('amount')).filter(total__gt=0)
            
            for p_sum in platform_sums:
                name = p_sum.get('provider_config__nickname') or p_sum.get('provider') or 'E-Cash'
                provider_balances.append({
                    'id': p_sum['provider_config'],
                    'name': name,
                    'balance': p_sum['total']
                })
                
            shop_balances.append({
                'shop': shop,
                'balance': balance,
                'provider_balances': provider_balances
            })
            total_ecash += balance
        
        context['shop_balances'] = shop_balances
        context['total_ecash'] = total_ecash
        context['tenant_ecash'] = ECashLedger.get_current_balance(tenant)
        return context


class ShopECashWithdrawView(LoginRequiredMixin, TemplateView):
    """
    Accountant view: Withdraw all e-cash from a specific shop.
    """
    template_name = 'payments/shop_ecash_withdraw.html'
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        # Only Accountant and Admin can access
        if request.user.role and request.user.role.name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, "Only accountants can withdraw e-cash.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.request.user.tenant
        shop_id = self.kwargs.get('shop_id')
        
        from apps.core.models import Location
        shop = get_object_or_404(Location, pk=shop_id, tenant=tenant, location_type='SHOP')
        
        context['shop'] = shop
        
        provider_config_id = self.request.GET.get('provider_config')
        if provider_config_id:
            from apps.payments.models import PaymentProviderConfig
            provider = get_object_or_404(PaymentProviderConfig, pk=provider_config_id)
            context['balance'] = ECashLedger.get_provider_balance(tenant, provider, shop)
            context['provider'] = provider
        else:
            context['balance'] = ECashLedger.get_shop_balance(tenant, shop)
            context['provider'] = None
            
        return context
    
    def post(self, request, shop_id):
        tenant = request.user.tenant
        
        from apps.core.models import Location
        shop = get_object_or_404(Location, pk=shop_id, tenant=tenant, location_type='SHOP')
        
        provider_config_id = request.GET.get('provider_config')
        provider = None
        if provider_config_id:
            from apps.payments.models import PaymentProviderConfig
            provider = get_object_or_404(PaymentProviderConfig, pk=provider_config_id)
            balance = ECashLedger.get_provider_balance(tenant, provider, shop)
            notes_suffix = f" via {provider.nickname}"
        else:
            balance = ECashLedger.get_shop_balance(tenant, shop)
            notes_suffix = ""
        
        if balance <= 0:
            messages.warning(request, f"No e-cash to withdraw from {shop.name}.")
            return redirect('payments:shop_ecash_list')
        
        # Create and complete withdrawal
        with transaction.atomic():
            withdrawal = ECashWithdrawal.objects.create(
                tenant=tenant,
                amount=balance,
                withdrawn_by=request.user,
                shop=shop,
                provider_config=provider,
                notes=f"E-Cash withdrawal from {shop.name}{notes_suffix}"
            )
            withdrawal.complete(request.user)
            
            # Notify shop manager if any
            from apps.notifications.models import Notification
            shop_managers = shop.users.filter(role__name='SHOP_MANAGER')
            for manager in shop_managers:
                Notification.objects.create(
                    tenant=tenant,
                    user=manager,
                    title="E-Cash Withdrawn",
                    message=f"E-cash balance of {balance} was withdrawn from {shop.name} by {request.user.get_full_name() or request.user.email}.",
                    notification_type='SYSTEM',
                    reference_type='ECashWithdrawal',
                    reference_id=withdrawal.pk
                )
        
        messages.success(
            request, 
            f"Successfully withdrawn {balance} e-cash from {shop.name}. "
            f"The shop's e-cash balance is now 0."
        )
        return redirect('payments:shop_ecash_list')


class ShopECashHistoryView(LoginRequiredMixin, SortableMixin, ListView):
    """
    Shop Manager view: View e-cash transaction history for their shop.
    """
    model = ECashLedger
    template_name = 'payments/shop_ecash_history.html'
    context_object_name = 'transactions'
    paginate_by = 30
    sortable_fields = ['created_at', 'amount', 'transaction_type', 'reference']
    default_sort = '-created_at'
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        # Shop managers, accountants, auditors, admins, and cashiers can access
        allowed_roles = ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_CASHIER']
        if request.user.role and request.user.role.name not in allowed_roles:
            messages.error(request, "You don't have permission to view e-cash history.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        from datetime import datetime
        user = self.request.user
        tenant = user.tenant
        
        # Get the shop to filter by
        shop = self._get_shop()
        if not shop:
            return ECashLedger.objects.none()
        
        queryset = ECashLedger.objects.filter(
            tenant=tenant,
            shop=shop
        ).select_related('created_by')
        
        # Apply filters
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        min_amount = self.request.GET.get('min_amount')
        max_amount = self.request.GET.get('max_amount')
        
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass
                
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass
                
        if min_amount:
            try:
                queryset = queryset.filter(amount__gte=Decimal(min_amount))
            except (ValueError, TypeError):
                pass
                
        if max_amount:
            try:
                queryset = queryset.filter(amount__lte=Decimal(max_amount))
            except (ValueError, TypeError):
                pass
                
        return self.apply_sorting(queryset)
    
    def _get_shop(self):
        """Get the shop to show history for."""
        user = self.request.user
        tenant = user.tenant
        shop_id = self.request.GET.get('shop')
        
        from apps.core.models import Location
        
        # Accountants/Auditors/Admins can view any shop
        if user.role and user.role.name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            if shop_id:
                return Location.objects.filter(
                    pk=shop_id, tenant=tenant, location_type='SHOP'
                ).first()
            # Default to first shop if none specified
            return Location.objects.filter(
                tenant=tenant, location_type='SHOP', is_active=True
            ).first()
        
        # Shop managers see their assigned shop
        if user.location and user.location.location_type == 'SHOP':
            return user.location
        
        return None
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        tenant = user.tenant
        shop = self._get_shop()
        
        context['shop'] = shop
        context['shop_balance'] = ECashLedger.get_shop_balance(tenant, shop) if shop else Decimal('0')
        
        # Pass filters to context
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['min_amount'] = self.request.GET.get('min_amount', '')
        context['max_amount'] = self.request.GET.get('max_amount', '')
        
        # For accountants/auditors, provide shop selector
        if user.role and user.role.name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            from apps.core.models import Location
            context['all_shops'] = Location.objects.filter(
                tenant=tenant, location_type='SHOP', is_active=True
            ).order_by('name')
        
        return context

from django.views import View

class ShopECashExportView(LoginRequiredMixin, View):
    """Export shop e-cash history to Excel or PDF."""
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        allowed_roles = ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_CASHIER']
        if request.user.role and request.user.role.name not in allowed_roles:
            messages.error(request, "You don't have permission to export e-cash history.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
        
    def get(self, request):
        from datetime import datetime
        from apps.core.excel_utils import create_export_workbook, build_excel_response
        from apps.core.pdf_utils import export_to_pdf
        from apps.core.models import Location
        
        user = request.user
        tenant = user.tenant
        
        shop_id = request.GET.get('shop')
        role_name = user.role.name if user.role else None
        
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            if shop_id:
                shop = Location.objects.filter(pk=shop_id, tenant=tenant, location_type='SHOP').first()
            else:
                shop = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True).first()
        else:
            shop = user.location if (user.location and user.location.location_type == 'SHOP') else None
            
        if not shop:
            messages.error(request, 'Shop not found.')
            return redirect('payments:shop_ecash_history')
            
        queryset = ECashLedger.objects.filter(tenant=tenant, shop=shop).select_related('created_by')
        
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        min_amount = request.GET.get('min_amount')
        max_amount = request.GET.get('max_amount')
        
        date_range_str = "All Time"
        if date_from and date_to:
            date_range_str = f"{date_from} to {date_to}"
        elif date_from:
            date_range_str = f"From {date_from}"
        elif date_to:
            date_range_str = f"Until {date_to}"
            
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass
        if min_amount:
            try:
                queryset = queryset.filter(amount__gte=Decimal(min_amount))
            except (ValueError, TypeError):
                pass
        if max_amount:
            try:
                queryset = queryset.filter(amount__lte=Decimal(max_amount))
            except (ValueError, TypeError):
                pass
                
        # Sort
        sort_by = request.GET.get('sort', 'created_at')
        direction = request.GET.get('dir', 'desc')
        if direction == 'desc':
            sort_by = f"-{sort_by}"
            
        valid_sorts = ['created_at', '-created_at', 'amount', '-amount', 'transaction_type', '-transaction_type', 'reference', '-reference']
        if sort_by in valid_sorts:
            queryset = queryset.order_by(sort_by)
        else:
            queryset = queryset.order_by('-created_at')
            
        headers = ['Date', 'Type', 'Amount', 'Reference', 'By', 'Notes']
        rows = []
        for t in queryset:
            ref = f"{t.reference_type} #{t.reference_id}" if (t.reference_type and t.reference_id) else (t.paystack_reference or "-")
            by = t.created_by.get_full_name() or t.created_by.email if t.created_by else 'System'
            rows.append([
                t.created_at.strftime('%Y-%m-%d %H:%M'),
                t.get_transaction_type_display(),
                float(t.amount),
                ref,
                by,
                t.notes or '-'
            ])
            
        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop.name,
                'date_range': date_range_str
            }
            return export_to_pdf('shop_ecash_history.pdf', 'Shop E-Cash History', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('E-Cash History', headers, rows)
            return build_excel_response(wb, 'shop_ecash_history.xlsx')

class ECashLedgerExportView(LoginRequiredMixin, View):
    """Export global e-cash ledger to Excel or PDF."""
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        allowed_roles = ['ACCOUNTANT', 'AUDITOR', 'ADMIN']
        if request.user.role and request.user.role.name not in allowed_roles:
            messages.error(request, "You don't have permission to export the ledger.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
        
    def get(self, request):
        from datetime import datetime
        from apps.core.excel_utils import create_export_workbook, build_excel_response
        from apps.core.pdf_utils import export_to_pdf
        
        user = request.user
        tenant = user.tenant
        
        queryset = ECashLedger.objects.filter(tenant=tenant).select_related('created_by', 'shop')
        
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        min_amount = request.GET.get('min_amount')
        max_amount = request.GET.get('max_amount')
        shop_id = request.GET.get('shop')
        
        date_range_str = "All Time"
        if date_from and date_to:
            date_range_str = f"{date_from} to {date_to}"
        elif date_from:
            date_range_str = f"From {date_from}"
        elif date_to:
            date_range_str = f"Until {date_to}"
            
        shop_name = "All Shops"
        if shop_id:
            from apps.core.models import Location
            loc = Location.objects.filter(pk=shop_id, tenant=tenant).first()
            if loc: shop_name = loc.name
            queryset = queryset.filter(shop_id=shop_id)
            
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass
        if min_amount:
            try:
                queryset = queryset.filter(amount__gte=Decimal(min_amount))
            except (ValueError, TypeError):
                pass
        if max_amount:
            try:
                queryset = queryset.filter(amount__lte=Decimal(max_amount))
            except (ValueError, TypeError):
                pass
                
        # Sort
        sort_by = request.GET.get('sort', 'created_at')
        direction = request.GET.get('dir', 'desc')
        if direction == 'desc':
            sort_by = f"-{sort_by}"
            
        valid_sorts = ['created_at', '-created_at', 'amount', '-amount', 'transaction_type', '-transaction_type', 'reference', '-reference', 'shop__name', '-shop__name']
        if sort_by in valid_sorts:
            queryset = queryset.order_by(sort_by)
        else:
            queryset = queryset.order_by('-created_at')
            
        headers = ['Date', 'Type', 'Shop', 'Reference', 'Amount', 'Balance After', 'Notes']
        rows = []
        for t in queryset:
            ref = f"{t.reference_type} #{t.reference_id}" if (t.reference_type and t.reference_id) else (t.paystack_reference or "-")
            rows.append([
                t.created_at.strftime('%Y-%m-%d %H:%M'),
                t.get_transaction_type_display(),
                t.shop.name if t.shop else '-',
                ref,
                float(t.amount),
                float(t.balance_after),
                t.notes or '-'
            ])
            
        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop_name,
                'date_range': date_range_str
            }
            return export_to_pdf('ecash_ledger.pdf', 'E-Cash Ledger', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('E-Cash Ledger', headers, rows)
            return build_excel_response(wb, 'ecash_ledger.xlsx')


@csrf_exempt
@require_POST
def paystack_webhook(request):
    """
    Handle Paystack webhook events.
    Verifies signature and processes payment confirmations.
    """
    # Get signature from headers
    signature = request.headers.get('X-Paystack-Signature', '')
    payload = request.body
    
    try:
        # Parse payload
        data = json.loads(payload)
        event = data.get('event', '')
        event_data = data.get('data', {})
        
        # Get tenant from metadata
        metadata = event_data.get('metadata', {})
        tenant_id = metadata.get('tenant_id')
        
        if not tenant_id:
            # Can't process without tenant
            return HttpResponse(status=200)  # Return 200 to stop retries
        
        from apps.core.models import Tenant
        try:
            tenant = Tenant.objects.get(pk=tenant_id)
        except Tenant.DoesNotExist:
            return HttpResponse(status=200)
        
        # Get payment provider settings
        settings = PaymentProviderConfig.objects.filter(
            tenant=tenant,
            provider='PAYSTACK',
            is_active=True
        ).first()
        
        if not settings:
            return HttpResponse(status=200)
        
        # Verify signature
        provider = PaystackProvider(settings)
        if not provider.verify_webhook_signature(payload, signature):
            return HttpResponse(status=400)  # Invalid signature
        
        # Handle events
        if event == 'charge.success':
            reference = event_data.get('reference', '')
            amount = Decimal(event_data.get('amount', 0)) / 100  # Convert from kobo
            
            # Find the sale by paystack reference
            from apps.sales.models import Sale
            sale = Sale.objects.filter(
                tenant=tenant,
                paystack_reference=reference
            ).first()
            
            if sale and sale.status == 'PENDING':
                # Complete the sale
                with transaction.atomic():
                    sale.status = 'COMPLETED'
                    sale.paystack_status = 'success'
                    sale.completed_at = timezone.now()
                    sale.save()
                    
                    # Record in e-cash ledger
                    ECashLedger.record_payment(
                        tenant=tenant,
                        amount=amount,
                        sale=sale,
                        paystack_ref=reference,
                        user=sale.attendant
                    )
        
        return HttpResponse(status=200)
    
    except json.JSONDecodeError:
        return HttpResponse(status=400)
    except Exception as e:
        # Log error but return 200 to prevent retries
        logger.error(f"Paystack webhook error: {e}")
        return HttpResponse(status=200)
