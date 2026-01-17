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

from .models import PaymentProviderSettings, ECashLedger, ECashWithdrawal
from .services.paystack import get_payment_provider, PaystackProvider
from apps.core.decorators import role_required

logger = logging.getLogger(__name__)


class PaymentProviderSettingsView(LoginRequiredMixin, TemplateView):
    """View and update payment provider settings."""
    template_name = 'payments/provider_settings.html'
    
    def dispatch(self, request, *args, **kwargs):
        # Only Admin can access
        if request.user.role and request.user.role.name != 'ADMIN':
            messages.error(request, "Only administrators can access payment settings.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.request.user.tenant
        
        # Get or create Paystack settings
        settings, created = PaymentProviderSettings.objects.get_or_create(
            tenant=tenant,
            provider='PAYSTACK',
            defaults={'is_active': False}
        )
        
        context['provider_settings'] = settings
        context['masked_secret_key'] = settings.get_masked_secret_key()
        context['ecash_balance'] = ECashLedger.get_current_balance(tenant)
        return context
    
    def post(self, request):
        tenant = request.user.tenant
        
        settings, created = PaymentProviderSettings.objects.get_or_create(
            tenant=tenant,
            provider='PAYSTACK',
            defaults={'is_active': False}
        )
        
        # Update settings
        settings.is_active = request.POST.get('is_active') == 'on'
        settings.test_mode = request.POST.get('test_mode') == 'on'
        settings.public_key = request.POST.get('public_key', '').strip()
        
        # Only update secret key if a new one is provided
        new_secret_key = request.POST.get('secret_key', '').strip()
        if new_secret_key and not new_secret_key.startswith('sk_'):
            # Provided but doesn't look like a valid key - might be masked
            pass
        elif new_secret_key:
            settings.secret_key = new_secret_key
        
        # Webhook secret (optional)
        new_webhook_secret = request.POST.get('webhook_secret', '').strip()
        if new_webhook_secret:
            settings.webhook_secret = new_webhook_secret
        
        settings.save()
        
        messages.success(request, "Payment provider settings updated successfully!")
        return redirect('payments:provider_settings')


@login_required
@require_POST
def test_connection(request):
    """Test connection to payment provider."""
    tenant = request.user.tenant
    
    try:
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


class ECashLedgerView(LoginRequiredMixin, ListView):
    """E-Cash transaction history."""
    model = ECashLedger
    template_name = 'payments/ecash_ledger.html'
    context_object_name = 'transactions'
    paginate_by = 50
    
    def get_queryset(self):
        return ECashLedger.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('created_by', 'shop').order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ecash_balance'] = ECashLedger.get_current_balance(self.request.user.tenant)
        return context


class ShopECashListView(LoginRequiredMixin, TemplateView):
    """
    Accountant view: List all shops with their E-Cash balances.
    Allows withdrawing e-cash from specific shops.
    """
    template_name = 'payments/shop_ecash_list.html'
    
    def dispatch(self, request, *args, **kwargs):
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
        for shop in shops:
            balance = ECashLedger.get_shop_balance(tenant, shop)
            shop_balances.append({
                'shop': shop,
                'balance': balance
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
        context['balance'] = ECashLedger.get_shop_balance(tenant, shop)
        return context
    
    def post(self, request, shop_id):
        tenant = request.user.tenant
        
        from apps.core.models import Location
        shop = get_object_or_404(Location, pk=shop_id, tenant=tenant, location_type='SHOP')
        
        balance = ECashLedger.get_shop_balance(tenant, shop)
        
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
                notes=f"E-Cash withdrawal from {shop.name}"
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


class ShopECashHistoryView(LoginRequiredMixin, ListView):
    """
    Shop Manager view: View e-cash transaction history for their shop.
    """
    model = ECashLedger
    template_name = 'payments/shop_ecash_history.html'
    context_object_name = 'transactions'
    paginate_by = 30
    
    def dispatch(self, request, *args, **kwargs):
        # Shop managers, accountants, auditors, and admins can access
        allowed_roles = ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN']
        if request.user.role and request.user.role.name not in allowed_roles:
            messages.error(request, "You don't have permission to view e-cash history.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        user = self.request.user
        tenant = user.tenant
        
        # Get the shop to filter by
        shop = self._get_shop()
        if not shop:
            return ECashLedger.objects.none()
        
        return ECashLedger.objects.filter(
            tenant=tenant,
            shop=shop
        ).select_related('created_by').order_by('-created_at')
    
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
        
        # For accountants/auditors, provide shop selector
        if user.role and user.role.name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            from apps.core.models import Location
            context['all_shops'] = Location.objects.filter(
                tenant=tenant, location_type='SHOP', is_active=True
            ).order_by('name')
        
        return context


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
        settings = PaymentProviderSettings.objects.filter(
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
