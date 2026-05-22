"""
Views for the subscriptions app.
Handles subscription status, payment history, receipts, and pricing page.
"""
from django.views import View
from django.views.generic import TemplateView, ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone

from apps.core.decorators import AdminRequiredMixin
from .models import SubscriptionPlan, SubscriptionPayment, TenantPricingOverride
from .services.pdf_service import PDFReceiptService
from decimal import Decimal


class SubscriptionStatusView(LoginRequiredMixin, AdminRequiredMixin, TemplateView):
    """
    Display subscription status for tenant admins.
    """
    template_name = 'subscriptions/status.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.request.user.tenant
        
        context['tenant'] = tenant
        context['plan'] = tenant.subscription_plan
        context['shop_count'] = tenant.get_shop_count()
        context['monthly_price'] = tenant.get_monthly_subscription_price()
        
        # Get recent payments
        context['recent_payments'] = SubscriptionPayment.objects.filter(
            tenant=tenant,
            status='COMPLETED'
        ).order_by('-created_at')[:5]
        
        # Subscription status info
        context['is_expiring_soon'] = (
            tenant.days_until_expiry is not None and 
            tenant.days_until_expiry <= 30 and 
            tenant.days_until_expiry > 0
        )
        context['is_expired'] = (
            tenant.days_until_expiry is not None and 
            tenant.days_until_expiry <= 0
        )
        
        return context


class SubscriptionHistoryView(LoginRequiredMixin, AdminRequiredMixin, ListView):
    """
    Display payment history for tenant admins.
    """
    template_name = 'subscriptions/history.html'
    context_object_name = 'payments'
    paginate_by = 20
    
    def get_queryset(self):
        return SubscriptionPayment.objects.filter(
            tenant=self.request.user.tenant
        ).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tenant'] = self.request.user.tenant
        return context


class ReceiptDownloadView(LoginRequiredMixin, AdminRequiredMixin, View):
    """
    Download PDF receipt for a payment.
    """
    
    def get(self, request, pk):
        payment = get_object_or_404(
            SubscriptionPayment,
            pk=pk,
            tenant=request.user.tenant,
            status='COMPLETED'
        )
        
        if not PDFReceiptService.is_available():
            messages.error(request, "PDF generation is not available. Please contact support.")
            return redirect('subscriptions:history')
        
        try:
            pdf_content = PDFReceiptService.generate_receipt(payment)
            filename = PDFReceiptService.get_receipt_filename(payment)
            
            response = HttpResponse(pdf_content, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            messages.error(request, f"Error generating receipt: {str(e)}")
            return redirect('subscriptions:history')


class ReceiptViewView(LoginRequiredMixin, AdminRequiredMixin, View):
    """
    Return receipt details as JSON for modal display.
    """
    
    def get(self, request, pk):
        payment = get_object_or_404(
            SubscriptionPayment,
            pk=pk,
            tenant=request.user.tenant,
            status='COMPLETED'
        )
        tenant = payment.tenant
        
        data = {
            'success': True,
            'receipt_number': payment.receipt_number,
            'payment_type': payment.get_payment_type_display(),
            'payment_method': payment.get_payment_method_display(),
            'status': payment.get_status_display(),
            'amount': str(payment.amount),
            'currency': payment.currency,
            'currency_symbol': tenant.currency_symbol,
            'plan_name': payment.plan_name or 'N/A',
            'period_start': payment.period_start.strftime('%B %d, %Y') if payment.period_start else None,
            'period_end': payment.period_end.strftime('%B %d, %Y') if payment.period_end else None,
            'transaction_reference': payment.transaction_reference or 'N/A',
            'created_at': payment.created_at.strftime('%B %d, %Y %I:%M %p'),
            'notes': payment.notes or '',
            'tenant_name': tenant.name,
            'download_url': f'/subscriptions/receipt/{payment.pk}/download/',
        }
        
        return JsonResponse(data)


class PricingPageView(TemplateView):
    """
    Public pricing page showing subscription plans.
    """
    template_name = 'subscriptions/pricing.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get active subscription plans
        context['plans'] = SubscriptionPlan.objects.filter(
            is_active=True
        ).order_by('display_order', 'base_price')
        
        # Mark which plan the user has (if logged in and has tenant)
        if self.request.user.is_authenticated and self.request.user.tenant:
            context['current_plan'] = self.request.user.tenant.subscription_plan
        
        return context


# ============== TENANT MANAGER VIEWS ==============

class TenantManagerRequiredMixin:
    """Mixin to ensure user is a Tenant Manager."""
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('core:login')
        if not request.user.role or request.user.role.name != 'TENANT_MANAGER':
            messages.error(request, "Access denied. This area is for Tenant Managers only.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)


class TenantManagerDashboardView(LoginRequiredMixin, TenantManagerRequiredMixin, TemplateView):
    """
    Dashboard for Tenant Managers showing their assigned tenants.
    """
    template_name = 'subscriptions/tm_dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import TenantManagerAssignment
        from apps.core.models import Tenant
        
        # Get assigned tenants
        assignments = TenantManagerAssignment.objects.filter(
            manager=self.request.user
        ).select_related('tenant', 'tenant__subscription_plan')
        
        context['assignments'] = assignments
        
        # Calculate stats
        tenants = [a.tenant for a in assignments]
        context['total_tenants'] = len(tenants)
        context['active_tenants'] = sum(1 for t in tenants if t.subscription_status == 'ACTIVE')
        context['expiring_soon'] = sum(1 for t in tenants if t.days_until_expiry and 0 < t.days_until_expiry <= 14)
        context['expired_tenants'] = sum(1 for t in tenants if t.subscription_status in ['EXPIRED', 'INACTIVE'])
        context['plans'] = SubscriptionPlan.objects.filter(is_active=True)
        
        return context


class TenantManagerTenantDetailView(LoginRequiredMixin, TenantManagerRequiredMixin, View):
    """
    View details of an assigned tenant.
    """
    template_name = 'subscriptions/tm_tenant_detail.html'
    
    def get(self, request, pk):
        from .models import TenantManagerAssignment
        from apps.core.models import Tenant
        
        # Verify this manager is assigned to this tenant
        assignment = get_object_or_404(
            TenantManagerAssignment,
            manager=request.user,
            tenant_id=pk
        )
        
        tenant = assignment.tenant
        
        # Get payment history
        payments = SubscriptionPayment.objects.filter(
            tenant=tenant
        ).order_by('-created_at')[:10]
        
        return render(request, self.template_name, {
            'tenant': tenant,
            'assignment': assignment,
            'payments': payments,
            'plans': SubscriptionPlan.objects.filter(is_active=True),
        })


class TenantManagerRecordPaymentView(LoginRequiredMixin, TenantManagerRequiredMixin, View):
    """
    Record a payment for an assigned tenant.
    """
    template_name = 'subscriptions/tm_record_payment.html'
    
    def get_tenant(self, pk):
        from .models import TenantManagerAssignment
        assignment = get_object_or_404(
            TenantManagerAssignment,
            manager=self.request.user,
            tenant_id=pk
        )
        return assignment.tenant
    
    def get(self, request, pk):
        tenant = self.get_tenant(pk)
        return render(request, self.template_name, {
            'tenant': tenant,
            'plans': SubscriptionPlan.objects.filter(is_active=True),
        })
    
    def post(self, request, pk):
        from datetime import timedelta
        from decimal import Decimal
        
        tenant = self.get_tenant(pk)
        
        payment_type = request.POST.get('payment_type')
        amount = request.POST.get('amount')
        payment_method = request.POST.get('payment_method')
        reference = request.POST.get('reference', '')
        months = int(request.POST.get('months', 1))
        subscription_plan_id = request.POST.get('subscription_plan')
        shop_count = int(request.POST.get('shop_count', 0))
        
        try:
            amount = Decimal(amount)
        except:
            messages.error(request, "Invalid amount.")
            return redirect('subscriptions:tm_record_payment', pk=pk)
        
        # Get the selected subscription plan for subscription/renewal payments
        plan_name = ''
        selected_plan = None
        if payment_type in ['SUBSCRIPTION', 'RENEWAL'] and subscription_plan_id:
            try:
                selected_plan = SubscriptionPlan.objects.get(pk=subscription_plan_id, is_active=True)
                plan_name = selected_plan.name
                # Assign the plan to the tenant
                tenant.subscription_plan = selected_plan
            except SubscriptionPlan.DoesNotExist:
                messages.error(request, "Invalid subscription plan selected.")
                return redirect('subscriptions:tm_record_payment', pk=pk)
        elif tenant.subscription_plan:
            plan_name = tenant.subscription_plan.name
            selected_plan = tenant.subscription_plan
        
        # Handle shop count for Premium plans
        shops_paid = 0
        if selected_plan and selected_plan.code == 'PREMIUM' and shop_count > 0:
            # Calculate extra shops (beyond the 5 included)
            extra_shops = max(0, shop_count - selected_plan.max_shops)
            
            if payment_type in ['SUBSCRIPTION', 'RENEWAL']:
                # Set the tenant's additional_shops to the extra count
                tenant.additional_shops = extra_shops
                shops_paid = shop_count
            elif payment_type == 'SHOP_TOPUP':
                # Add to existing additional_shops
                current_shops = tenant.get_shop_count()
                shops_to_add = max(0, shop_count - current_shops)
                tenant.additional_shops = (tenant.additional_shops or 0) + shops_to_add
                shops_paid = shops_to_add
        
        # Create payment record
        payment = SubscriptionPayment.objects.create(
            tenant=tenant,
            payment_type=payment_type,
            amount=amount,
            currency=tenant.currency,
            status='COMPLETED',
            payment_method=payment_method,
            transaction_reference=reference,
            plan_name=plan_name,
            shops_paid=shops_paid,
            created_by=request.user,
            notes=f"Recorded by Tenant Manager: {request.user.email}"
        )
        
        # Update subscription dates
        if payment_type == 'ONBOARDING':
            tenant.onboarding_paid = True
            tenant.save(update_fields=['onboarding_paid'])
        elif payment_type in ['SUBSCRIPTION', 'RENEWAL']:
            today = timezone.now().date()
            if tenant.subscription_end_date and tenant.subscription_end_date > today:
                tenant.subscription_end_date = tenant.subscription_end_date + timedelta(days=30 * months)
            else:
                tenant.subscription_start_date = today
                tenant.subscription_end_date = today + timedelta(days=30 * months)
            
            tenant.subscription_status = 'ACTIVE'
            tenant.is_active = True
            tenant.save()
            
            payment.period_start = tenant.subscription_start_date
            payment.period_end = tenant.subscription_end_date
            payment.save()
        elif payment_type == 'SHOP_TOPUP':
            # Just save the tenant with updated additional_shops
            tenant.save(update_fields=['additional_shops'])
        
        # Send payment confirmation email
        from .services.notification_service import NotificationService
        success, error = NotificationService.send_payment_confirmation(payment)
        if not success:
            # Log but don't fail - payment was still recorded
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to send payment confirmation email: {error}")
        
        messages.success(request, f"Payment of {tenant.currency_symbol}{amount} recorded successfully.")
        return redirect('subscriptions:tm_tenant_detail', pk=pk)


class TenantManagerPaymentHistoryView(LoginRequiredMixin, TenantManagerRequiredMixin, ListView):
    """
    View payment history for an assigned tenant.
    """
    template_name = 'subscriptions/tm_payment_history.html'
    context_object_name = 'payments'
    paginate_by = 20
    
    def get_queryset(self):
        from .models import TenantManagerAssignment
        
        assignment = get_object_or_404(
            TenantManagerAssignment,
            manager=self.request.user,
            tenant_id=self.kwargs['pk']
        )
        self.tenant = assignment.tenant
        
        return SubscriptionPayment.objects.filter(
            tenant=self.tenant
        ).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tenant'] = self.tenant
        return context


# ============== PROFORMA INVOICE VIEWS ==============

class ProformaAccessMixin:
    """Mixin to allow access to Superusers and Tenant Managers."""
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('core:login')
        
        is_su = request.user.is_superuser
        is_tm = request.user.role and request.user.role.name == 'TENANT_MANAGER'
        
        if not (is_su or is_tm):
            messages.error(request, "Access denied. Only Superusers or Tenant Managers can generate proforma invoices.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)


class ProformaInvoicePrintView(ProformaAccessMixin, View):
    """
    Renders a Proforma Invoice HTML that can be printed.
    Expects POST request from a form to render it in a new tab.
    """
    template_name = 'subscriptions/proforma_invoice.html'
    
    def post(self, request):
        client_name = request.POST.get('client_name', 'Potential Client')
        client_email = request.POST.get('client_email', '')
        client_phone = request.POST.get('client_phone', '')
        client_company = request.POST.get('client_company', '')
        
        plan_id = request.POST.get('subscription_plan')
        billing_cycle = request.POST.get('billing_cycle', 'monthly')
        shop_count = int(request.POST.get('shop_count', 1))
        
        try:
            onboarding_fee = Decimal(request.POST.get('onboarding_fee', '4500.00'))
        except:
            onboarding_fee = Decimal('4500.00')
            
        try:
            discount = Decimal(request.POST.get('discount', '0'))
        except:
            discount = Decimal('0')
            
        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        
        is_annual = (billing_cycle == 'annual')
        
        # Calculate Base Pricing
        base = plan.annual_base_price if is_annual and plan.annual_base_price else plan.base_price
        shop_extra = plan.annual_additional_shop_price if is_annual and plan.annual_additional_shop_price else plan.additional_shop_price
        
        additional_shops = max(0, shop_count - plan.max_shops) if plan.code == 'PREMIUM' else 0
        additional_shop_cost = additional_shops * shop_extra
        
        subtotal = base + additional_shop_cost
        
        # Multiply by 12 if annual
        subscription_total = subtotal * 12 if is_annual else subtotal
        
        discount_amount = (subscription_total * discount) / 100
        
        total_due = subscription_total - discount_amount + onboarding_fee
        
        invoice_date = timezone.now().date()
        from datetime import timedelta
        valid_until = invoice_date + timedelta(days=14)
        
        # Generate random proforma number
        import uuid
        proforma_number = f"PF-{timezone.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

        context = {
            'client_name': client_name,
            'client_email': client_email,
            'client_phone': client_phone,
            'client_company': client_company,
            'plan': plan,
            'billing_cycle': billing_cycle,
            'is_annual': is_annual,
            'shop_count': shop_count,
            'base_price': base,
            'additional_shops': additional_shops,
            'additional_shop_cost': additional_shop_cost,
            'subtotal': subtotal,
            'subscription_total': subscription_total,
            'onboarding_fee': onboarding_fee,
            'discount': discount,
            'discount_amount': discount_amount,
            'total_due': total_due,
            'invoice_date': invoice_date,
            'valid_until': valid_until,
            'proforma_number': proforma_number,
        }
        
        return render(request, self.template_name, context)


