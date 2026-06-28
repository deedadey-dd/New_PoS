"""
Views for the accounting app.
Handles cash transfers between shop managers and accountants.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.views import View
from django.views.generic import ListView, TemplateView, CreateView, DetailView
from django.db.models import Q, Sum, Count
from django.http import JsonResponse
from django.urls import reverse_lazy
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal

from .models import CashTransfer, ExpenditureRequest, ExpenditureCategory, ExpenditureItem
from .forms import (
    CashTransferForm, BankTransferForm,
    ExpenditureRequestForm, ExpenditureItemForm, ExpenditureItemFormSet, ExpenditureCategoryForm,
)
from apps.core.models import User, Location
from apps.core.mixins import SortableMixin


class CashTransferListView(LoginRequiredMixin, SortableMixin, ListView):
    """List cash transfers for the current user."""
    model = CashTransfer
    template_name = 'accounting/cash_transfer_list.html'
    context_object_name = 'transfers'
    paginate_by = 20
    sortable_fields = ['created_at', 'amount', 'from_location__name', 'to_location__name', 'status']
    default_sort = '-created_at'
    paginate_by = 20
    
    def get_queryset(self):
        from datetime import datetime
        
        user = self.request.user
        role_name = user.role.name if user.role else None
        
        # Accountants, Auditors, and Admin see ALL transfers
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            queryset = CashTransfer.objects.filter(
                tenant=user.tenant
            ).select_related(
                'from_user', 'to_user', 'from_location', 'to_location'
            ).order_by('-created_at')
        else:
            # Others see only their own transfers
            queryset = CashTransfer.objects.filter(
                tenant=user.tenant
            ).filter(
                Q(from_user=user) | Q(to_user=user)
            ).select_related(
                'from_user', 'to_user', 'from_location', 'to_location'
            ).order_by('-created_at')
        
        # Date range filter
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        
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
        
        # Status filter
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # Shop filter (for accountants/auditors)
        shop = self.request.GET.get('shop')
        if shop:
            queryset = queryset.filter(
                Q(from_location_id=shop) | Q(to_location_id=shop)
            )
            
        # User filters
        sender = self.request.GET.get('sender')
        if sender:
            queryset = queryset.filter(from_user_id=sender)
            
        receiver = self.request.GET.get('receiver')
        if receiver:
            queryset = queryset.filter(to_user_id=receiver)
        
        return self.apply_sorting(queryset)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role_name = user.role.name if user.role else None
        
        # Count pending transfers for user to confirm
        context['pending_count'] = CashTransfer.objects.filter(
            tenant=user.tenant,
            to_user=user,
            status='PENDING'
        ).count()
        
        # Check if user can create transfers (Auditor cannot create)
        context['can_create'] = role_name in ['SHOP_ATTENDANT', 'SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN', 'SHOP_CASHIER']
        
        # Show filters for all roles
        context['show_filters'] = True
        
        # For accountants/auditors/admin: full view with shop dropdown + summary cards
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            context['is_full_view'] = True
            context['shops'] = Location.objects.filter(
                tenant=user.tenant,
                location_type='SHOP',
                is_active=True
            )
            context['can_send_to_shops'] = user.tenant.allow_accountant_to_shop_transfers if user.tenant else False
            
            # Cash deposit summary
            today = timezone.now().date()
            context['today_deposits'] = CashTransfer.objects.filter(
                tenant=user.tenant,
                transfer_type='DEPOSIT',
                status='CONFIRMED',
                confirmed_at__date=today
            ).aggregate(
                total=Sum('amount'),
                count=Count('id')
            )
        
        # Preserve filter values for all filtered views
        if context.get('show_filters'):
            context['date_from'] = self.request.GET.get('date_from', '')
            context['date_to'] = self.request.GET.get('date_to', '')
            context['selected_shop'] = self.request.GET.get('shop', '')
            context['selected_status'] = self.request.GET.get('status', '')
            context['selected_sender'] = self.request.GET.get('sender', '')
            context['selected_receiver'] = self.request.GET.get('receiver', '')
            
            from apps.core.models import User
            context['users'] = User.objects.filter(tenant=user.tenant, is_active=True).order_by('first_name', 'last_name', 'email')
        
        return context


class CashTransferReceiptView(LoginRequiredMixin, View):
    """Printable receipt for a cash transfer."""
    template_name = 'accounting/cash_transfer_receipt.html'
    
    def get(self, request, pk):
        transfer = get_object_or_404(CashTransfer, pk=pk, tenant=request.user.tenant)
        
        # Security check: User must be involved in the transfer or have financial role
        role_name = request.user.role.name if request.user.role else ''
        is_involved = request.user == transfer.from_user or request.user == transfer.to_user
        has_financial_role = role_name in ['ADMIN', 'ACCOUNTANT', 'AUDITOR', 'SHOP_MANAGER', 'SHOP_CASHIER']
        
        if not (is_involved or has_financial_role):
            from django.contrib import messages
            messages.error(request, "You don't have permission to view this transfer.")
            return redirect('core:dashboard')
            
        return render(request, self.template_name, {
            'transfer': transfer,
            'current_tenant': request.user.tenant,
        })


from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

@login_required
def api_cash_transfer_detail(request, pk):
    """Return JSON details for a cash transfer."""
    try:
        transfer = CashTransfer.objects.get(pk=pk, tenant=request.user.tenant)
        
        # Security check
        role_name = request.user.role.name if request.user.role else ''
        is_involved = request.user == transfer.from_user or request.user == transfer.to_user
        has_financial_role = role_name in ['ADMIN', 'ACCOUNTANT', 'AUDITOR', 'SHOP_MANAGER', 'SHOP_CASHIER']
        
        if not (is_involved or has_financial_role):
            return JsonResponse({'error': 'Permission denied'}, status=403)
            
        data = {
            'id': transfer.pk,
            'amount': str(transfer.amount),
            'status': transfer.get_status_display(),
            'transfer_type': transfer.get_transfer_type_display(),
            'from_user': transfer.from_user.get_full_name() or transfer.from_user.email,
            'to_user': transfer.to_user.get_full_name() or transfer.to_user.email,
            'from_location': transfer.from_location.name if transfer.from_location else 'N/A',
            'to_location': transfer.to_location.name if transfer.to_location else 'N/A',
            'created_at': transfer.created_at.strftime('%b %d, %Y %H:%M'),
            'confirmed_at': transfer.confirmed_at.strftime('%b %d, %Y %H:%M') if transfer.confirmed_at else None,
            'cancelled_at': transfer.cancelled_at.strftime('%b %d, %Y %H:%M') if transfer.cancelled_at else None,
            'notes': transfer.notes,
            'cancellation_reason': transfer.cancellation_reason,
            'destination': transfer.get_destination_display()
        }
        return JsonResponse(data)
    except CashTransfer.DoesNotExist:
        return JsonResponse({'error': 'Transfer not found'}, status=404)


class CashTransferCreateView(LoginRequiredMixin, View):
    """Create a new cash transfer."""
    template_name = 'accounting/cash_transfer_form.html'
    
    def dispatch(self, request, *args, **kwargs):
        # Shop Attendants, Shop Managers, Cashiers, Accountants and Admins can create transfers
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['SHOP_ATTENDANT', 'SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN', 'SHOP_CASHIER']:
            messages.error(request, 'You do not have permission to create cash transfers.')
            return redirect('accounting:cash_transfer_list')
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        form = CashTransferForm(user=request.user)
        return render(request, self.template_name, {'form': form})
    
    def post(self, request):
        form = CashTransferForm(request.POST, user=request.user)
        if form.is_valid():
            transfer = form.save(commit=False)
            transfer.tenant = request.user.tenant
            transfer.from_user = request.user
            transfer.from_location = request.user.location
            
            # Set transfer type and destination based on sender's role
            role_name = request.user.role.name if request.user.role else None
            if role_name == 'ACCOUNTANT':
                transfer.transfer_type = 'FLOAT'
            else:
                transfer.transfer_type = 'DEPOSIT'

            # Enforce destination from form's clean() logic
            transfer.destination = form.cleaned_data.get('destination', 'ACCOUNTANT')

            transfer.save()
            
            # Create notification for recipient
            from apps.notifications.models import Notification
            Notification.objects.create(
                tenant=transfer.tenant,
                user=transfer.to_user,
                title="Incoming Cash Transfer",
                message=f"{request.user.get_full_name() or request.user.email} is sending you {transfer.tenant.currency_symbol}{transfer.amount}. Please confirm receipt.",
                notification_type='SYSTEM',
                reference_type='CashTransfer',
                reference_id=transfer.pk
            )
            
            messages.success(request, f'Cash transfer of {transfer.amount} created. Waiting for confirmation.')
            return redirect('accounting:cash_transfer_list')
        
        return render(request, self.template_name, {'form': form})


class CashTransferConfirmView(LoginRequiredMixin, View):
    """Confirm receipt of a cash transfer."""
    
    def post(self, request, pk):
        transfer = get_object_or_404(
            CashTransfer,
            pk=pk,
            tenant=request.user.tenant
        )
        
        try:
            transfer.confirm(request.user)
            messages.success(request, f'Cash transfer of {transfer.amount} confirmed!')
        except ValidationError as e:
            messages.error(request, str(e))
        
        return redirect('accounting:cash_transfer_list')


class CashTransferCancelView(LoginRequiredMixin, View):
    """Cancel a cash transfer."""
    
    def post(self, request, pk):
        transfer = get_object_or_404(
            CashTransfer,
            pk=pk,
            tenant=request.user.tenant
        )
        
        reason = request.POST.get('reason', '')
        
        # Only sender or admin can cancel
        role_name = request.user.role.name if request.user.role else None
        if transfer.from_user != request.user and role_name != 'ADMIN':
            messages.error(request, 'You do not have permission to cancel this transfer.')
            return redirect('accounting:cash_transfer_list')
        
        try:
            transfer.cancel(request.user, reason)
            messages.success(request, 'Cash transfer cancelled.')
        except ValidationError as e:
            messages.error(request, str(e))
        
        return redirect('accounting:cash_transfer_list')


class AccountantDashboardView(LoginRequiredMixin, View):
    """
    Accountant financial dashboard showing all financial transactions.
    """
    template_name = 'accounting/accountant_dashboard.html'
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN', 'SHOP_CASHIER', 'AUDITOR']:
            messages.error(request, 'Only accountants, auditors, and cashiers can access this dashboard.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        from apps.sales.models import Sale
        
        user = request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None
        
        if role_name == 'SHOP_CASHIER':
            if not user.location or user.location.location_type != 'SHOP':
                messages.error(request, 'You must be assigned to a shop to access the Cashier Dashboard.')
                return redirect('core:dashboard')
                
            pending_invoices = Sale.objects.filter(
                tenant=tenant,
                shop=user.location,
                status='PENDING',
                payment_method='PENDING_INVOICE'
            ).select_related('attendant', 'customer').order_by('-created_at')
            
            recent_sales = Sale.objects.filter(
                tenant=tenant,
                shop=user.location,
                status__in=['COMPLETED', 'PENDING_DISPATCH']
            ).select_related('attendant', 'customer').order_by('-created_at')
            
            today_sales_total = Sale.objects.filter(
                tenant=tenant,
                shop=user.location,
                status__in=['COMPLETED', 'PENDING_DISPATCH'],
                completed_at__date=timezone.now().date()
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')
            
            pending_invoices_agg = pending_invoices.aggregate(total=Sum('total'))
            pending_invoices_total = pending_invoices_agg['total'] or Decimal('0')
            
            pending_deposits_agg = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status='PENDING'
            ).aggregate(total=Sum('amount'), count=Count('id'))
            
            pending_deposits_count = pending_deposits_agg['count'] or 0
            pending_deposits_total = pending_deposits_agg['total'] or Decimal('0')
            
            from apps.payments.models import ShopPaymentAssignment, PaymentProviderConfig
            
            configs_to_process = []
            if user.location and user.location.location_type == 'SHOP':
                assignments = ShopPaymentAssignment.objects.filter(
                    shop=user.location,
                    provider_config__is_active=True
                ).select_related('provider_config').order_by('priority')
                configs_to_process = [a.provider_config for a in assignments]
            else:
                configs_to_process = PaymentProviderConfig.objects.filter(
                    tenant=tenant,
                    is_active=True
                ).order_by('nickname')
                
            available_providers = []
            for config in configs_to_process:
                provider_cls = None
                
                if config.provider == 'PAYSTACK':
                    from apps.payments.services.paystack import PaystackProvider
                    provider_cls = PaystackProvider(config)
                elif config.provider == 'EXPRESSPAY':
                    from apps.payments.services.expresspay import ExpressPayProvider
                    provider_cls = ExpressPayProvider(config)
                elif config.provider == 'APPSNMOBILE':
                    from apps.payments.services.appsnmobile import AppsnMobileProvider
                    provider_cls = AppsnMobileProvider(config)
                elif config.provider == 'NALOPAY':
                    from apps.payments.services.nalopay import NalopayProvider
                    provider_cls = NalopayProvider(config)
                
                if provider_cls:
                    available_providers.append({
                        'id': config.id,
                        'name': config.nickname,
                        'provider': config.provider,
                        'checkout_type': getattr(provider_cls, 'get_checkout_type', 'redirect')
                    })
                    
            # Deduplicate IDs just in case
            seen_ids = set()
            unique_providers = []
            for p in available_providers:
                if p['id'] not in seen_ids:
                    seen_ids.add(p['id'])
                    unique_providers.append(p)
            
            context = {
                'pending_invoices': pending_invoices[:10],
                'pending_invoices_count': pending_invoices.count(),
                'pending_invoices_total': pending_invoices_total,
                'recent_sales': recent_sales[:10],
                'today_sales_total': today_sales_total,
                'pending_deposits_count': pending_deposits_count,
                'pending_deposits_total': pending_deposits_total,
                'shop': user.location,
                'available_providers': unique_providers,
            }
            return render(request, 'accounting/cashier_dashboard.html', context)
        
        # Date range filter
        date_range = request.GET.get('range', 'today')
        date_from_str = request.GET.get('date_from')
        date_to_str = request.GET.get('date_to')
        today = timezone.now().date()
        
        # Custom date range takes priority
        if date_from_str and date_to_str:
            try:
                start_date = datetime.strptime(date_from_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(date_to_str, '%Y-%m-%d').date()
                date_range = 'custom'
            except ValueError:
                start_date = today
                end_date = today
                date_range = 'today'
        else:
            end_date = today
            if date_range == 'week':
                start_date = today - timedelta(days=7)
            elif date_range == 'month':
                start_date = today - timedelta(days=30)
            elif date_range == 'all':
                start_date = None
                end_date = None
            else:
                start_date = today
                date_range = 'today'
        
        def get_date_filter(field_name='created_at__date'):
            q = Q()
            if start_date:
                q &= Q(**{f'{field_name}__gte': start_date})
            if end_date:
                q &= Q(**{f'{field_name}__lte': end_date})
            return q
        
        context = {
            'current_range': date_range,
            'date_from': start_date,
            'date_to': end_date,
        }
        
        # ===== SALES SUMMARY =====
        sales_filter = Q(tenant=tenant, status__in=['COMPLETED', 'PENDING_DISPATCH']) & get_date_filter()
        if role_name == 'SHOP_CASHIER' and user.location:
            sales_filter &= Q(shop=user.location)
        
        context['sales_summary'] = Sale.objects.filter(sales_filter).aggregate(
            total_revenue=Sum('total'),
            total_count=Count('id'),
            cash_total=Sum('total', filter=Q(payment_method='CASH')),
            ecash_total=Sum('total', filter=Q(payment_method='ECASH')),
            momo_total=Sum('total', filter=Q(payment_method='MOMO')),
            credit_total=Sum('total', filter=Q(payment_method='CREDIT')),
        )
        
        # Sales by shop will be built from location_summary below
        
        # ===== CASH DEPOSITS =====
        deposit_filter = Q(tenant=tenant, transfer_type='DEPOSIT') & get_date_filter()
        if role_name == 'SHOP_CASHIER' and user.location:
            deposit_filter &= Q(from_location=user.location)
        
        context['deposits_summary'] = CashTransfer.objects.filter(deposit_filter).aggregate(
            pending_amount=Sum('amount', filter=Q(status='PENDING')),
            pending_count=Count('id', filter=Q(status='PENDING')),
            confirmed_amount=Sum('amount', filter=Q(status='CONFIRMED')),
            confirmed_count=Count('id', filter=Q(status='CONFIRMED')),
        )
        
        # Deposits by shop will be built from location_summary below
        
        # Recent confirmed deposits
        recent_deposits_qs = CashTransfer.objects.filter(
            tenant=tenant,
            transfer_type='DEPOSIT',
            status='CONFIRMED'
        )
        if role_name == 'SHOP_CASHIER' and user.location:
            recent_deposits_qs = recent_deposits_qs.filter(from_location=user.location)
        context['recent_deposits'] = recent_deposits_qs.select_related('from_user', 'from_location').order_by('-confirmed_at')[:10]
        
        # Pending deposits awaiting confirmation
        context['pending_deposits'] = CashTransfer.objects.filter(
            tenant=tenant,
            transfer_type='DEPOSIT',
            status='PENDING',
            to_user=user
        ).select_related('from_user', 'from_location').order_by('-created_at')
        
        # ===== LOCATION ACTIVITY SUMMARY =====
        # Per-shop breakdown: total sales, cash/ecash/credit, deposits, est. cash on hand
        shops = Location.objects.filter(
            tenant=tenant, location_type='SHOP', is_active=True
        )
        if role_name == 'SHOP_CASHIER' and user.location:
            shops = shops.filter(id=user.location.id)
        
        from apps.sales.models import Sale
        
        location_summary = []
        for shop in shops:
            shop_sales = Sale.objects.filter(
                Q(tenant=tenant, shop=shop, status__in=['COMPLETED', 'PENDING_DISPATCH']) & get_date_filter()
            )
            
            sales_agg = shop_sales.aggregate(
                total_revenue=Sum('total'),
                cash_sales=Sum('total', filter=Q(payment_method='CASH')),
                ecash_sales=Sum('total', filter=Q(payment_method='ECASH')),
                credit_sales=Sum('total', filter=Q(payment_method='CREDIT')),
                mixed_paid=Sum('amount_paid', filter=Q(payment_method='MIXED')),
                sale_count=Count('id'),
            )
            
            total_revenue = sales_agg['total_revenue'] or Decimal('0')
            cash_sales = (sales_agg['cash_sales'] or Decimal('0')) + (sales_agg['mixed_paid'] or Decimal('0'))
            ecash_sales = sales_agg['ecash_sales'] or Decimal('0')
            credit_sales = sales_agg['credit_sales'] or Decimal('0')
            
            # Deposits from this shop (confirmed)
            deposits = CashTransfer.objects.filter(
                Q(tenant=tenant, from_location=shop, transfer_type='DEPOSIT', status='CONFIRMED') & 
                ~Q(to_user__role__name__in=['SHOP_MANAGER', 'SHOP_ATTENDANT', 'SHOP_CASHIER']) & 
                get_date_filter()
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            expenditures = CashTransfer.objects.filter(
                Q(tenant=tenant, from_location=shop, transfer_type='EXPENDITURE', status='CONFIRMED') & get_date_filter()
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Floats sent TO this shop
            floats_received = CashTransfer.objects.filter(
                Q(tenant=tenant, to_location=shop, transfer_type='FLOAT', status='CONFIRMED') & get_date_filter()
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            # Customer Cash Payments
            from apps.customers.models import CustomerTransaction
            customer_payments = CustomerTransaction.objects.filter(
                Q(tenant=tenant, performed_by__location=shop, transaction_type='CREDIT', description__icontains='(CASH)') &
                ~Q(description__icontains='ECASH') & ~Q(description__icontains='MOMO') &
                get_date_filter('created_at__date')
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            est_cash_on_hand = cash_sales + customer_payments + floats_received - deposits - expenditures
            
            location_summary.append({
                'shop_name': shop.name,
                'total_revenue': total_revenue,
                'cash_sales': cash_sales,
                'ecash_sales': ecash_sales,
                'credit_sales': credit_sales,
                'deposits': deposits,
                'floats_received': floats_received,
                'est_cash_on_hand': est_cash_on_hand,
                'sale_count': sales_agg['sale_count'] or 0,
            })
        
        context['location_summary'] = location_summary
        
        # Build sales_by_shop and deposits_by_shop from location_summary to guarantee 0-value shops appear
        sales_by_shop = [
            {'shop__name': loc['shop_name'], 'revenue': loc['total_revenue'], 'count': loc['sale_count']}
            for loc in location_summary
        ]
        sales_by_shop.sort(key=lambda x: x['revenue'], reverse=True)
        context['sales_by_shop'] = sales_by_shop
        
        # For deposits, location_summary aggregates the total. We need the count too.
        # It's okay to just query it properly without dropping shops
        for loc in location_summary:
            shop_obj = next(s for s in shops if s.name == loc['shop_name'])
            loc['deposits_count'] = CashTransfer.objects.filter(
                Q(tenant=tenant, from_location=shop_obj, transfer_type='DEPOSIT', status='CONFIRMED') & get_date_filter()
            ).count()
            
        deposits_by_shop = [
            {'from_location__name': loc['shop_name'], 'total': loc['deposits'], 'count': loc['deposits_count']}
            for loc in location_summary
        ]
        deposits_by_shop.sort(key=lambda x: x['total'], reverse=True)
        context['deposits_by_shop'] = deposits_by_shop
        
        # ===== USER ACTIVITY SUMMARY =====
        from apps.core.models import User as TenantUser
        
        shop_users = TenantUser.objects.filter(
            tenant=tenant,
            role__name__in=['SHOP_MANAGER', 'SHOP_ATTENDANT'],
            is_active=True
        ).select_related('role', 'location')
        if role_name == 'SHOP_CASHIER' and user.location:
            shop_users = shop_users.filter(location=user.location)
        
        sales_by_user = []
        for u in shop_users:
            user_sales = Sale.objects.filter(
                Q(tenant=tenant, attendant=u, status__in=['COMPLETED', 'PENDING_DISPATCH']) & get_date_filter()
            )
            
            user_agg = user_sales.aggregate(
                total_revenue=Sum('total'),
                cash_sales=Sum('total', filter=Q(payment_method='CASH')),
                ecash_sales=Sum('total', filter=Q(payment_method='ECASH')),
                credit_sales=Sum('total', filter=Q(payment_method='CREDIT')),
                sale_count=Count('id'),
            )
            
            total_rev = user_agg['total_revenue'] or Decimal('0')
            if total_rev > 0 or user_agg['sale_count'] > 0:
                sales_by_user.append({
                    'user_name': u.get_full_name() or u.email,
                    'location': u.location.name if u.location else '-',
                    'role': u.role.get_name_display() if u.role else '-',
                    'total_revenue': total_rev,
                    'cash_sales': user_agg['cash_sales'] or Decimal('0'),
                    'ecash_sales': user_agg['ecash_sales'] or Decimal('0'),
                    'credit_sales': user_agg['credit_sales'] or Decimal('0'),
                    'sale_count': user_agg['sale_count'] or 0,
                })
        
        context['sales_by_user'] = sales_by_user
        
        return render(request, self.template_name, context)


class SalesReportView(LoginRequiredMixin, View):
    """
    Detailed sales report with multi-dimensional filtering.
    For accountants to analyze sales by day, shop, attendant, product.
    """
    template_name = 'accounting/sales_report.html'
    
    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, 'Only accountants can access this report.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        from apps.sales.models import Sale, SaleItem
        from apps.inventory.models import Product
        
        user = request.user
        tenant = user.tenant
        
        # Date filter
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        today = timezone.now().date()
        
        if not date_from:
            date_from = today - timedelta(days=7)
        else:
            date_from = timezone.datetime.strptime(date_from, '%Y-%m-%d').date()
        
        if not date_to:
            date_to = today
        else:
            date_to = timezone.datetime.strptime(date_to, '%Y-%m-%d').date()
        
        # Base queryset
        sales = Sale.objects.filter(
            tenant=tenant,
            status__in=['COMPLETED', 'PENDING_DISPATCH'],
            created_at__date__gte=date_from,
            created_at__date__lte=date_to
        ).select_related('shop', 'attendant', 'shift')
        
        # Shop filter
        shop_id = request.GET.get('shop')
        if shop_id:
            sales = sales.filter(shop_id=shop_id)
        
        # Attendant filter
        attendant_id = request.GET.get('attendant')
        if attendant_id:
            sales = sales.filter(attendant_id=attendant_id)
        
        # Payment method filter
        payment = request.GET.get('payment')
        if payment:
            sales = sales.filter(payment_method=payment)
        
        # Summary
        summary = sales.aggregate(
            total_revenue=Sum('total'),
            total_count=Count('id'),
            cash_total=Sum('total', filter=Q(payment_method='CASH')),
            ecash_total=Sum('total', filter=Q(payment_method='ECASH')),
        )
        
        # By day
        sales_by_day = sales.values('created_at__date').annotate(
            revenue=Sum('total'),
            count=Count('id')
        ).order_by('-created_at__date')
        
        # By shop
        sales_by_shop = sales.values('shop__id', 'shop__name').annotate(
            revenue=Sum('total'),
            count=Count('id')
        ).order_by('-revenue')
        
        # By attendant
        sales_by_attendant = sales.values(
            'attendant__id', 'attendant__first_name', 'attendant__last_name', 'attendant__email'
        ).annotate(
            revenue=Sum('total'),
            count=Count('id')
        ).order_by('-revenue')
        
        # Top products (from sale items) - top 10 for quick insight
        top_products = SaleItem.objects.filter(
            sale__in=sales
        ).values('product__id', 'product__name').annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total')
        ).order_by('-revenue')[:10]
        
        # Full product breakdown (all products sold in period)
        all_products = SaleItem.objects.filter(
            sale__in=sales
        ).values('product__id', 'product__name').annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total')
        ).order_by('product__name')  # Alphabetical for easier scanning
        
        # Calculate totals for full product table
        all_products_totals = SaleItem.objects.filter(
            sale__in=sales
        ).aggregate(
            total_qty=Sum('quantity'),
            total_revenue=Sum('total')
        )
        
        # Get filter options
        from apps.core.models import Location, User as CoreUser
        
        context = {
            'date_from': date_from,
            'date_to': date_to,
            'summary': summary,
            'sales_by_day': sales_by_day,
            'sales_by_shop': sales_by_shop,
            'sales_by_attendant': sales_by_attendant,
            'top_products': top_products,
            'all_products': all_products,
            'all_products_total_qty': all_products_totals['total_qty'] or 0,
            'all_products_total_revenue': all_products_totals['total_revenue'] or Decimal('0'),
            'sales_count': sales.count(),
            # Filter options
            'shops': Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True),
            'attendants': CoreUser.objects.filter(
                tenant=tenant, 
                role__name__in=['SHOP_ATTENDANT', 'SHOP_MANAGER'], 
                is_active=True
            ),
            'payment_methods': [
                ('CASH', 'Cash'),
                ('ECASH', 'E-Cash'),
                ('MOMO', 'Local Momo'),
                ('CREDIT', 'Credit'),
            ],
            'selected_payment': payment,
        }
        
        return render(request, self.template_name, context)


from apps.core.mixins import SortableMixin

class PriceHistoryView(LoginRequiredMixin, SortableMixin, View):
    """
    Audit trail of all shop price changes.
    """
    template_name = 'accounting/price_history.html'
    sortable_fields = ['product__name', 'location__name', 'selling_price', 'created_at']
    default_sort = '-created_at'
    
    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            messages.error(request, 'Only accountants and auditors can access price history.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        from apps.inventory.models import ShopPrice, Product
        from apps.core.models import Location
        
        user = request.user
        tenant = user.tenant
        
        # Get all shop prices (including inactive = history)
        prices = ShopPrice.objects.filter(
            tenant=tenant
        ).select_related('product', 'location').order_by('-created_at')
        
        # Shop filter
        shop_id = request.GET.get('shop')
        if shop_id:
            prices = prices.filter(location_id=shop_id)
        
        # Product filter
        product_id = request.GET.get('product')
        if product_id:
            prices = prices.filter(product_id=product_id)
        
        # Date filter
        date_from = request.GET.get('date_from')
        if date_from:
            prices = prices.filter(created_at__date__gte=date_from)
            
        prices = self.apply_sorting(prices)
        
        context = {
            'prices': prices[:100],  # Limit for performance
            'total_count': prices.count(),
            'shops': Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True),
            'products': Product.objects.filter(tenant=tenant, is_active=True)[:50],
            'current_sort': self.request.GET.get('sort', ''),
            'current_dir': self.request.GET.get('dir', 'asc'),
        }
        
        return render(request, self.template_name, context)


class CashTransferExportView(LoginRequiredMixin, View):
    """Export cash transfers to Excel."""

    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            messages.error(request, 'You do not have permission to export cash transfers.')
            return redirect('accounting:cash_transfer_list')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from datetime import datetime
        from apps.core.excel_utils import create_export_workbook, build_excel_response
        from apps.core.pdf_utils import export_to_pdf

        user = request.user
        queryset = CashTransfer.objects.filter(
            tenant=user.tenant
        ).select_related(
            'from_user', 'to_user', 'from_location', 'to_location'
        ).order_by('-created_at')

        # Apply same filters as list view
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        
        date_range_str = "All Time"
        if date_from and date_to:
            date_range_str = f"{date_from} to {date_to}"
        elif date_from:
            date_range_str = f"From {date_from}"
        elif date_to:
            date_range_str = f"Until {date_to}"

        if date_from:
            try:
                queryset = queryset.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                pass
        if date_to:
            try:
                queryset = queryset.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                pass

        status = request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        shop_name = "All Locations"
        shop = request.GET.get('shop')
        if shop:
            from apps.core.models import Location
            loc = Location.objects.filter(pk=shop).first()
            if loc:
                shop_name = loc.name
            queryset = queryset.filter(
                Q(from_location_id=shop) | Q(to_location_id=shop)
            )

        headers = ['Date', 'From User', 'From Location', 'To User', 'To Location',
                    'Amount', 'Type', 'Status', 'Confirmed At', 'Notes']
        rows = []
        for t in queryset:
            rows.append([
                t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else '',
                t.from_user.get_full_name() or t.from_user.email if t.from_user else '',
                t.from_location.name if t.from_location else '',
                t.to_user.get_full_name() or t.to_user.email if t.to_user else '',
                t.to_location.name if t.to_location else '',
                float(t.amount) if t.amount else 0,
                t.get_transfer_type_display() if hasattr(t, 'get_transfer_type_display') else t.transfer_type,
                t.get_status_display() if hasattr(t, 'get_status_display') else t.status,
                t.confirmed_at.strftime('%Y-%m-%d %H:%M') if t.confirmed_at else '',
                t.notes or '',
            ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop_name,
                'date_range': date_range_str
            }
            return export_to_pdf('cash_transfers_export.pdf', 'Cash Transfers Report', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Cash Transfers', headers, rows)
            return build_excel_response(wb, 'cash_transfers_export.xlsx')


class SalesReportExportView(LoginRequiredMixin, View):
    """Export accountant's sales report to Excel."""

    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, 'Only accountants can export this report.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from apps.sales.models import Sale, SaleItem
        from apps.core.excel_utils import create_export_workbook, add_sheet, build_excel_response

        user = request.user
        tenant = user.tenant

        # Date filter (same as SalesReportView)
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        today = timezone.now().date()

        if not date_from:
            date_from = today - timedelta(days=7)
        else:
            try:
                date_from = timezone.datetime.strptime(date_from, '%Y-%m-%d').date()
            except ValueError:
                date_from = today - timedelta(days=7)

        if not date_to:
            date_to = today
        else:
            try:
                date_to = timezone.datetime.strptime(date_to, '%Y-%m-%d').date()
            except ValueError:
                date_to = today

        sales = Sale.objects.filter(
            tenant=tenant,
            status='COMPLETED',
            created_at__date__gte=date_from,
            created_at__date__lte=date_to
        ).select_related('shop', 'attendant')

        shop_name = "All Locations"
        shop_id = request.GET.get('shop')
        if shop_id:
            sales = sales.filter(shop_id=shop_id)
            from apps.core.models import Location
            loc = Location.objects.filter(pk=shop_id).first()
            if loc: shop_name = loc.name

        attendant_id = request.GET.get('attendant')
        if attendant_id:
            sales = sales.filter(attendant_id=attendant_id)

        payment = request.GET.get('payment')
        if payment:
            sales = sales.filter(payment_method=payment)

        # Sheet 1: Sales by Day
        sales_by_day = sales.values('created_at__date').annotate(
            revenue=Sum('total'),
            count=Count('id')
        ).order_by('-created_at__date')

        day_headers = ['Date', 'Revenue', 'Sales Count']
        day_rows = [
            [str(d['created_at__date']), float(d['revenue'] or 0), d['count']]
            for d in sales_by_day
        ]

        # Sheet 2: Product Breakdown
        all_products = SaleItem.objects.filter(
            sale__in=sales
        ).values('product__name').annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total')
        ).order_by('product__name')

        prod_headers = ['Product', 'Qty Sold', 'Revenue']
        prod_rows = [
            [p['product__name'], float(p['qty_sold'] or 0), float(p['revenue'] or 0)]
            for p in all_products
        ]

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop_name,
                'date_range': f"{date_from} to {date_to}"
            }
            return export_to_pdf(
                f'sales_report_{date_from}_{date_to}.pdf', 
                'Sales Report - Daily Summary', 
                day_headers, day_rows,
                additional_tables=[('Product Breakdown', prod_headers, prod_rows)],
                metadata=metadata
            )
        else:
            wb = create_export_workbook('Sales by Day', day_headers, day_rows)
            add_sheet(wb, 'Product Breakdown', prod_headers, prod_rows)
            return build_excel_response(wb, f'sales_report_{date_from}_to_{date_to}.xlsx')


class PriceHistoryExportView(LoginRequiredMixin, View):
    """Export price history to Excel."""

    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            messages.error(request, 'Only accountants and auditors can export price history.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from apps.inventory.models import ShopPrice
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        user = request.user
        tenant = user.tenant

        prices = ShopPrice.objects.filter(
            tenant=tenant
        ).select_related('product', 'location').order_by('-created_at')

        shop_name = "All Locations"
        shop_id = request.GET.get('shop')
        if shop_id:
            prices = prices.filter(location_id=shop_id)
            from apps.core.models import Location
            loc = Location.objects.filter(pk=shop_id).first()
            if loc: shop_name = loc.name

        product_id = request.GET.get('product')
        if product_id:
            prices = prices.filter(product_id=product_id)

        date_from = request.GET.get('date_from')
        date_range_str = "All Time"
        if date_from:
            prices = prices.filter(created_at__date__gte=date_from)
            date_range_str = f"From {date_from}"

        headers = ['Date', 'Product', 'Location', 'Selling Price', 'Active']
        rows = []
        for p in prices:
            rows.append([
                p.created_at.strftime('%Y-%m-%d %H:%M') if p.created_at else '',
                p.product.name if p.product else '',
                p.location.name if p.location else '',
                float(p.selling_price) if p.selling_price else 0,
                'Yes' if p.is_active else 'No',
            ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop_name,
                'date_range': date_range_str
            }
            return export_to_pdf(
                'price_history_export.pdf', 
                'Price History Report', 
                headers, rows,
                metadata=metadata
            )
        else:
            wb = create_export_workbook('Price History', headers, rows)
            return build_excel_response(wb, 'price_history_export.xlsx')

class DigitalPaymentConfirmationView(LoginRequiredMixin, View):
    """View and confirm unconfirmed E-Cash and Momo transactions."""
    
    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, 'Only accountants can confirm digital payments.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
        
    def get(self, request):
        from apps.sales.models import Sale
        from apps.customers.models import CustomerTransaction
        from django.db.models import Q
        
        tenant = request.user.tenant
        
        # Unconfirmed E-Cash
        ecash_sales = Sale.objects.filter(tenant=tenant, status='COMPLETED', payment_method='ECASH', is_accountant_confirmed=False).select_related('shop', 'attendant')
        ecash_cts = CustomerTransaction.objects.filter(tenant=tenant, transaction_type='CREDIT', description__icontains='ECASH', is_accountant_confirmed=False).select_related('customer', 'performed_by')
        
        # Unconfirmed Momo
        momo_sales = Sale.objects.filter(tenant=tenant, status='COMPLETED', payment_method='MOMO', is_accountant_confirmed=False).select_related('shop', 'attendant')
        momo_cts = CustomerTransaction.objects.filter(tenant=tenant, transaction_type='CREDIT', description__icontains='MOMO', is_accountant_confirmed=False).select_related('customer', 'performed_by')
        
        # Filters
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        payment_method = request.GET.get('payment_method', '')
        tx_type = request.GET.get('tx_type', '')
        provider_config_id = request.GET.get('provider_config', '')
        
        if date_from:
            ecash_sales = ecash_sales.filter(created_at__date__gte=date_from)
            ecash_cts = ecash_cts.filter(created_at__date__gte=date_from)
            momo_sales = momo_sales.filter(created_at__date__gte=date_from)
            momo_cts = momo_cts.filter(created_at__date__gte=date_from)
            
        if date_to:
            ecash_sales = ecash_sales.filter(created_at__date__lte=date_to)
            ecash_cts = ecash_cts.filter(created_at__date__lte=date_to)
            momo_sales = momo_sales.filter(created_at__date__lte=date_to)
            momo_cts = momo_cts.filter(created_at__date__lte=date_to)
            
        if payment_method == 'ECASH':
            momo_sales = momo_sales.none()
            momo_cts = momo_cts.none()
        elif payment_method == 'MOMO':
            ecash_sales = ecash_sales.none()
            ecash_cts = ecash_cts.none()
            
        if tx_type == 'SALE':
            ecash_cts = ecash_cts.none()
            momo_cts = momo_cts.none()
        elif tx_type == 'CUST_PAYMENT':
            ecash_sales = ecash_sales.none()
            momo_sales = momo_sales.none()

        # Filter E-Cash by provider when ECASH is selected and a provider_config is specified
        if provider_config_id and (payment_method == 'ECASH' or not payment_method):
            from apps.payments.models import ECashLedger
            # Get sale IDs linked to this provider via ECashLedger
            linked_sale_ids = ECashLedger.objects.filter(
                tenant=tenant,
                provider_config_id=provider_config_id,
                reference_type='Sale'
            ).values_list('reference_id', flat=True)
            ecash_sales = ecash_sales.filter(id__in=linked_sale_ids)

        from apps.payments.models import PaymentProviderConfig, ECashLedger
        providers = PaymentProviderConfig.objects.filter(tenant=tenant, is_active=True)

        # Build a map of sale_id -> provider_config.nickname for display in template
        sale_ids_in_view = list(ecash_sales.values_list('id', flat=True))
        ledger_entries = ECashLedger.objects.filter(
            tenant=tenant,
            reference_type='Sale',
            reference_id__in=sale_ids_in_view
        ).select_related('provider_config')
        sale_provider_map = {
            entry.reference_id: entry.provider_config.nickname if entry.provider_config else 'E-Cash'
            for entry in ledger_entries
        }

        # Combine all queries into a single list of transactions
        transactions = []

        for sale in ecash_sales:
            transactions.append({
                'tx_type': 'sale',
                'payment_method': 'E-Cash',
                'provider': sale_provider_map.get(sale.id, 'E-Cash'),
                'id': sale.id,
                'created_at': sale.created_at,
                'shop_name': sale.shop.name if sale.shop else '-',
                'performed_by': sale.attendant.get_full_name() or sale.attendant.email if sale.attendant else '-',
                'reference_number': sale.sale_number,
                'reference_id': sale.id,
                'amount': sale.amount_paid,
            })
            
        for ct in ecash_cts:
            transactions.append({
                'tx_type': 'ct',
                'payment_method': 'E-Cash',
                'provider': 'E-Cash',
                'id': ct.id,
                'created_at': ct.created_at,
                'shop_name': '-',
                'performed_by': ct.performed_by.get_full_name() or ct.performed_by.email if ct.performed_by else '-',
                'reference_number': ct.customer.name,
                'reference_id': ct.customer.id,
                'customer': ct.customer,
                'amount': ct.amount,
            })
            
        for sale in momo_sales:
            transactions.append({
                'tx_type': 'sale',
                'payment_method': 'Momo',
                'provider': 'Momo',
                'id': sale.id,
                'created_at': sale.created_at,
                'shop_name': sale.shop.name if sale.shop else '-',
                'performed_by': sale.attendant.get_full_name() or sale.attendant.email if sale.attendant else '-',
                'reference_number': sale.sale_number,
                'reference_id': sale.id,
                'amount': sale.amount_paid,
            })
            
        for ct in momo_cts:
            transactions.append({
                'tx_type': 'ct',
                'payment_method': 'Momo',
                'provider': 'Momo',
                'id': ct.id,
                'created_at': ct.created_at,
                'shop_name': '-',
                'performed_by': ct.performed_by.get_full_name() or ct.performed_by.email if ct.performed_by else '-',
                'reference_number': ct.customer.name,
                'reference_id': ct.customer.id,
                'customer': ct.customer,
                'amount': ct.amount,
            })
            
        transactions.sort(key=lambda x: x['created_at'], reverse=True)
        
        from django.core.paginator import Paginator
        paginator = Paginator(transactions, 50)  # Show 50 items per page
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'page_obj': page_obj,
            'date_from': date_from,
            'date_to': date_to,
            'payment_method': payment_method,
            'tx_type': tx_type,
            'providers': providers,
            'selected_provider': provider_config_id,
        }
        
        return render(request, 'accounting/digital_confirmations.html', context)
        
    def post(self, request):
        from apps.sales.models import Sale
        from apps.customers.models import CustomerTransaction
        from django.utils import timezone
        
        tenant = request.user.tenant
        sale_ids = request.POST.getlist('sale_ids')
        ct_ids = request.POST.getlist('ct_ids')
        
        single_sale_id = request.POST.get('single_sale_id')
        single_ct_id = request.POST.get('single_ct_id')
        
        if single_sale_id:
            sale_ids = [single_sale_id]
            ct_ids = []
        elif single_ct_id:
            ct_ids = [single_ct_id]
            sale_ids = []
            
        if not sale_ids and not ct_ids:
            messages.error(request, 'No transactions selected.')
            return redirect('accounting:digital_confirmations')
            
        now = timezone.now()
        
        # Update Sales
        if sale_ids:
            Sale.objects.filter(tenant=tenant, id__in=sale_ids, is_accountant_confirmed=False).update(
                is_accountant_confirmed=True,
                accountant_confirmed_at=now,
                accountant_confirmed_by=request.user
            )
            
        # Update Customer Transactions
        if ct_ids:
            CustomerTransaction.objects.filter(tenant=tenant, id__in=ct_ids, is_accountant_confirmed=False).update(
                is_accountant_confirmed=True,
                accountant_confirmed_at=now,
                accountant_confirmed_by=request.user
            )
            
        messages.success(request, f'Successfully confirmed {len(sale_ids) + len(ct_ids)} transactions.')
        return redirect('accounting:digital_confirmations')

class BankTransferCreateView(LoginRequiredMixin, View):
    """Create a new bank transfer."""
    
    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        
        # In strict sales workflow, Cashiers can perform bank transfers
        allowed_roles = ['ACCOUNTANT', 'ADMIN']
        if request.user.tenant.use_strict_sales_workflow:
            allowed_roles.append('SHOP_CASHIER')
            
        if role_name not in allowed_roles:
            messages.error(request, 'Only accountants can perform bank transfers.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
        
    def get(self, request):
        form = BankTransferForm(user=request.user)
        return render(request, 'accounting/bank_transfer_form.html', {'form': form})
        
    def post(self, request):
        form = BankTransferForm(request.POST, user=request.user)
        if form.is_valid():
            bank_transfer = form.save(commit=False)
            bank_transfer.tenant = request.user.tenant
            bank_transfer.accountant = request.user
            # Save the specific E-Cash provider if this is an E-Cash transfer
            provider_config_id = form.cleaned_data.get('provider_config')
            if bank_transfer.fund_source == 'ECASH' and provider_config_id:
                try:
                    from apps.payments.models import PaymentProviderConfig
                    bank_transfer.provider_config = PaymentProviderConfig.objects.get(
                        id=provider_config_id,
                        tenant=request.user.tenant
                    )
                except PaymentProviderConfig.DoesNotExist:
                    pass
            bank_transfer.save()
            messages.success(request, 'Bank transfer recorded successfully.')
            return redirect('accounting:bank_history')
        
        return render(request, 'accounting/bank_transfer_form.html', {'form': form})

class BankTransferReceiptView(LoginRequiredMixin, View):
    """Printable receipt for a bank transfer."""
    
    def get(self, request, pk):
        from .models import BankTransfer
        
        bank_transfer = get_object_or_404(BankTransfer, pk=pk, tenant=request.user.tenant)
        
        return render(request, 'accounting/bank_transfer_receipt.html', {
            'bank_transfer': bank_transfer,
            'tenant': request.user.tenant
        })

class ShopMomoListView(LoginRequiredMixin, TemplateView):
    """
    Accountant view: List all shops with their unconfirmed Local Momo balances.
    Allows bulk confirming (withdrawing) momo from specific shops.
    """
    template_name = 'accounting/shop_momo_list.html'
    
    def dispatch(self, request, *args, **kwargs):
        # Only Accountant and Admin can access
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, "Only accountants can access shop momo balances.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.request.user.tenant
        
        # Get all shops
        from apps.core.models import Location
        from apps.sales.models import Sale
        from apps.customers.models import CustomerTransaction
        from apps.accounting.models import DigitalFundWithdrawal
        from django.db.models import Sum, Q
        
        shops = Location.objects.filter(
            tenant=tenant,
            location_type='SHOP',
            is_active=True
        ).order_by('name')
        
        # Calculate momo balance for each shop
        shop_balances = []
        total_momo = Decimal('0')
        
        for shop in shops:
            # Sales Momo
            sales_momo = Sale.objects.filter(
                tenant=tenant, 
                shop=shop, 
                status='COMPLETED', 
                payment_method='MOMO'
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            
            # Customer Debt Payments Momo
            ct_momo = CustomerTransaction.objects.filter(
                tenant=tenant,
                performed_by__location=shop,
                transaction_type='CREDIT',
                description__icontains='MOMO'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Withdrawn Momo
            withdrawn_momo = DigitalFundWithdrawal.objects.filter(
                tenant=tenant,
                shop=shop,
                fund_source='MOMO'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            balance = (sales_momo + ct_momo) - withdrawn_momo
            
            shop_balances.append({
                'shop': shop,
                'balance': balance
            })
            total_momo += balance
        
        context['shop_balances'] = shop_balances
        context['total_momo'] = total_momo
        return context

class ShopMomoWithdrawView(LoginRequiredMixin, View):
    """
    Accountant action: Bulk confirm all unconfirmed momo transactions for a shop.
    """
    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, "Only accountants can withdraw momo funds.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
        
    def post(self, request, shop_id):
        tenant = request.user.tenant
        
        from apps.core.models import Location
        from apps.accounting.models import DigitalFundWithdrawal
        
        shop = get_object_or_404(Location, pk=shop_id, tenant=tenant, location_type='SHOP')
        
        amount_str = request.POST.get('amount')
        if not amount_str:
            messages.error(request, "Amount is required.")
            return redirect('accounting:shop_momo_list')
            
        try:
            from decimal import Decimal
            amount = Decimal(amount_str)
            if amount <= 0:
                raise ValueError("Amount must be positive.")
        except Exception:
            messages.error(request, "Invalid amount.")
            return redirect('accounting:shop_momo_list')
            
        DigitalFundWithdrawal.objects.create(
            tenant=tenant,
            shop=shop,
            accountant=request.user,
            amount=amount,
            fund_source='MOMO',
            notes=request.POST.get('notes', '')
        )
        
        messages.success(request, f"Successfully withdrew {tenant.currency_symbol}{amount} Local Momo from {shop.name}.")
        return redirect('accounting:shop_momo_list')

class ShopEcashListView(LoginRequiredMixin, TemplateView):
    """
    Accountant view: List all shops with their E-Cash balances.
    """
    template_name = 'accounting/shop_ecash_list.html'
    
    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, "Only accountants can access shop E-Cash balances.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.request.user.tenant
        
        from apps.core.models import Location
        from apps.payments.models import ECashLedger, PaymentProviderConfig
        from django.db.models import Sum
        
        shops = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True).order_by('name')
        providers = PaymentProviderConfig.objects.filter(tenant=tenant, is_active=True)
        
        shop_balances = []
        total_ecash_by_provider = {p.id: Decimal('0') for p in providers}
        total_ecash_by_provider['legacy'] = Decimal('0')
        total_ecash = Decimal('0')
        
        for shop in shops:
            shop_data = {'shop': shop, 'providers': {}}
            shop_total = Decimal('0')
            
            # E-Cash balances per provider for this shop
            ledger_balances = ECashLedger.objects.filter(
                tenant=tenant, shop=shop
            ).values('provider_config_id').annotate(total=Sum('amount'))
            
            for lb in ledger_balances:
                pid = lb['provider_config_id']
                amt = lb['total'] or Decimal('0')
                if pid in total_ecash_by_provider:
                    shop_data['providers'][pid] = shop_data['providers'].get(pid, Decimal('0')) + amt
                    total_ecash_by_provider[pid] += amt
                else:
                    # Legacy or inactive provider
                    shop_data['providers']['legacy'] = shop_data['providers'].get('legacy', Decimal('0')) + amt
                    total_ecash_by_provider['legacy'] += amt
                shop_total += amt
            
            provider_balances = []
            
            for provider in providers:
                pid = provider.id
                amt = shop_data['providers'].get(pid, Decimal('0'))
                provider_balances.append({
                    'id': pid,
                    'nickname': provider.nickname,
                    'balance': amt
                })
                
            legacy_amt = shop_data['providers'].get('legacy', Decimal('0'))
            if legacy_amt > 0:
                provider_balances.append({
                    'id': '',
                    'nickname': 'Legacy/Unknown',
                    'balance': legacy_amt
                })
                
            shop_data['provider_balances'] = provider_balances
            shop_data['balance'] = shop_total
            shop_balances.append(shop_data)
        
        context['providers'] = providers
        context['shop_balances'] = shop_balances
        context['total_ecash_by_provider'] = total_ecash_by_provider
        context['total_ecash'] = total_ecash
        return context

class ShopEcashWithdrawView(LoginRequiredMixin, View):
    """
    Accountant action: Withdraw partial/full E-Cash from a shop via Ledger.
    """
    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, "Only accountants can withdraw E-Cash funds.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
        
    def post(self, request, shop_id):
        tenant = request.user.tenant
        
        from apps.core.models import Location
        from apps.accounting.models import DigitalFundWithdrawal
        
        shop = get_object_or_404(Location, pk=shop_id, tenant=tenant, location_type='SHOP')
        
        amount_str = request.POST.get('amount')
        provider_config_id = request.POST.get('provider_config')
        
        if not amount_str:
            messages.error(request, "Amount is required.")
            return redirect('accounting:shop_ecash_list')
            
        provider_config = None
        if provider_config_id:
            from apps.payments.models import PaymentProviderConfig
            provider_config = PaymentProviderConfig.objects.filter(id=provider_config_id, tenant=tenant).first()
            
        try:
            from decimal import Decimal
            amount = Decimal(amount_str)
            if amount <= 0:
                raise ValueError("Amount must be positive.")
        except Exception:
            messages.error(request, "Invalid amount.")
            return redirect('accounting:shop_ecash_list')
            
        DigitalFundWithdrawal.objects.create(
            tenant=tenant,
            shop=shop,
            accountant=request.user,
            amount=amount,
            fund_source='ECASH',
            provider_config=provider_config,
            notes=request.POST.get('notes', '')
        )
        
        # Also record in ECashLedger to keep balance consistent
        from apps.payments.models import ECashLedger
        # We don't have an ECashWithdrawal object here, so we use None for reference
        ECashLedger.objects.create(
            tenant=tenant,
            transaction_type='WITHDRAWAL',
            amount=-abs(amount),
            reference_type='DigitalFundWithdrawal',
            reference_id=None,
            provider_config=provider_config,
            provider=provider_config.provider if provider_config else 'PAYSTACK',
            created_by=request.user,
            notes=request.POST.get('notes', '') or f"Withdrawal from {shop.name}",
            shop=shop
        )
        
        messages.success(request, f"Successfully withdrew {tenant.currency_symbol}{amount} E-Cash from {shop.name}.")
        return redirect('accounting:shop_ecash_list')


class ShopEcashHistoryView(LoginRequiredMixin, TemplateView):
    """
    Shop Manager / Accountant view: View e-cash transaction history for a shop.
    Mirrors ShopMomoHistoryView but for ECASH payment method.
    """
    template_name = 'accounting/shop_ecash_history.html'

    def dispatch(self, request, *args, **kwargs):
        allowed_roles = ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_CASHIER']
        role_name = request.user.role.name if request.user.role else None
        if role_name not in allowed_roles:
            messages.error(request, "You don't have permission to view e-cash history.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def _get_shop(self):
        user = self.request.user
        tenant = user.tenant
        shop_id = self.request.GET.get('shop')

        from apps.core.models import Location

        role_name = user.role.name if user.role else None
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            if shop_id:
                return Location.objects.filter(pk=shop_id, tenant=tenant, location_type='SHOP').first()
            return Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True).first()

        if user.location and user.location.location_type == 'SHOP':
            return user.location

        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        tenant = user.tenant
        shop = self._get_shop()

        from apps.sales.models import Sale
        from apps.customers.models import CustomerTransaction
        from django.db.models import Sum
        from datetime import datetime

        if shop:
            sales = Sale.objects.filter(
                tenant=tenant,
                shop=shop,
                status='COMPLETED',
                payment_method='ECASH'
            )

            cts = CustomerTransaction.objects.filter(
                tenant=tenant,
                performed_by__location=shop,
                transaction_type='CREDIT',
                description__icontains='ECASH'
            )

            # Apply filters
            date_from = self.request.GET.get('date_from')
            date_to = self.request.GET.get('date_to')
            min_amount = self.request.GET.get('min_amount')
            max_amount = self.request.GET.get('max_amount')

            if date_from:
                try:
                    date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                    sales = sales.filter(created_at__date__gte=date_from_parsed)
                    cts = cts.filter(created_at__date__gte=date_from_parsed)
                except ValueError:
                    pass

            if date_to:
                try:
                    date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                    sales = sales.filter(created_at__date__lte=date_to_parsed)
                    cts = cts.filter(created_at__date__lte=date_to_parsed)
                except ValueError:
                    pass

            if min_amount:
                try:
                    min_val = Decimal(min_amount)
                    sales = sales.filter(amount_paid__gte=min_val)
                    cts = cts.filter(amount__gte=min_val)
                except (ValueError, TypeError):
                    pass

            if max_amount:
                try:
                    max_val = Decimal(max_amount)
                    sales = sales.filter(amount_paid__lte=max_val)
                    cts = cts.filter(amount__lte=max_val)
                except (ValueError, TypeError):
                    pass

            # Balance calc from all-time ledger (not filtered)
            total_sales = Sale.objects.filter(
                tenant=tenant, shop=shop, status='COMPLETED', payment_method='ECASH'
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')

            total_cts = CustomerTransaction.objects.filter(
                tenant=tenant, performed_by__location=shop,
                transaction_type='CREDIT', description__icontains='ECASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            total_withdrawn = DigitalFundWithdrawal.objects.filter(
                tenant=tenant, shop=shop, fund_source='ECASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            context['shop_balance'] = (total_sales + total_cts) - total_withdrawn
            context['shop'] = shop

            # Combine sales + customer transactions
            transactions = list(sales) + list(cts)

            # Sorting
            sort_by = self.request.GET.get('sort', 'created_at')
            direction = self.request.GET.get('dir', 'desc')
            is_reverse = direction == 'desc'

            if sort_by == 'amount':
                transactions.sort(
                    key=lambda x: getattr(x, 'amount_paid', getattr(x, 'amount', 0)),
                    reverse=is_reverse
                )
            elif sort_by == 'type':
                transactions.sort(
                    key=lambda x: 'Sale' if hasattr(x, 'sale_number') else 'Debt Payment',
                    reverse=is_reverse
                )
            elif sort_by == 'status':
                transactions.sort(key=lambda x: x.is_accountant_confirmed, reverse=is_reverse)
            else:
                transactions.sort(key=lambda x: x.created_at, reverse=is_reverse)

            context['transactions'] = transactions
            context['date_from'] = date_from
            context['date_to'] = date_to
            context['min_amount'] = min_amount
            context['max_amount'] = max_amount
            context['current_sort'] = sort_by
            context['current_dir'] = direction

        role_name = user.role.name if user.role else None
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            from apps.core.models import Location
            context['all_shops'] = Location.objects.filter(
                tenant=tenant, location_type='SHOP', is_active=True
            ).order_by('name')

        return context


class ShopEcashExportView(LoginRequiredMixin, View):
    """Export e-cash history to Excel or PDF."""

    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_MANAGER', 'SHOP_CASHIER']:
            messages.error(request, 'You do not have permission to export this report.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        from apps.sales.models import Sale
        from apps.customers.models import CustomerTransaction
        from apps.core.models import Location
        from datetime import datetime
        from apps.core.excel_utils import create_export_workbook, build_excel_response

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
            return redirect('accounting:shop_ecash_history')

        sales = Sale.objects.filter(tenant=tenant, shop=shop, status='COMPLETED', payment_method='ECASH')
        cts = CustomerTransaction.objects.filter(
            tenant=tenant, performed_by__location=shop,
            transaction_type='CREDIT', description__icontains='ECASH'
        )

        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')

        date_range_str = "All Time"
        if date_from and date_to:
            date_range_str = f"{date_from} to {date_to}"
        elif date_from:
            date_range_str = f"From {date_from}"
        elif date_to:
            date_range_str = f"Until {date_to}"

        if date_from:
            try:
                sales = sales.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
                cts = cts.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                pass
        if date_to:
            try:
                sales = sales.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
                cts = cts.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                pass

        headers = ['Date', 'Type', 'Reference', 'Amount', 'Confirmed']
        rows = []
        for s in sales.order_by('-created_at'):
            rows.append([
                s.created_at.strftime('%Y-%m-%d %H:%M') if s.created_at else '',
                'E-Cash Sale',
                s.sale_number,
                float(s.amount_paid),
                'Yes' if s.is_accountant_confirmed else 'No',
            ])
        for ct in cts.order_by('-created_at'):
            rows.append([
                ct.created_at.strftime('%Y-%m-%d %H:%M') if ct.created_at else '',
                'Debt Payment (E-Cash)',
                str(ct.customer) if ct.customer else '',
                float(ct.amount),
                'Yes' if ct.is_accountant_confirmed else 'No',
            ])

        wb = create_export_workbook('E-Cash History', headers, rows)
        return build_excel_response(wb, f'ecash_history_{shop.name}_{date_from or "all"}.xlsx')


class ShopMomoHistoryView(LoginRequiredMixin, TemplateView):
    """
    Shop Manager view: View momo transaction history for their shop.
    """
    template_name = 'accounting/shop_momo_history.html'


    def dispatch(self, request, *args, **kwargs):
        allowed_roles = ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_CASHIER']
        role_name = request.user.role.name if request.user.role else None
        if role_name not in allowed_roles:
            messages.error(request, "You don't have permission to view momo history.")
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
        
    def _get_shop(self):
        user = self.request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None
        shop_id = self.request.GET.get('shop')
        
        from apps.core.models import Location
        
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            if shop_id:
                return Location.objects.filter(pk=shop_id, tenant=tenant, location_type='SHOP').first()
            return None
            
        if user.location and user.location.location_type == 'SHOP':
            return user.location
            
        return None
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        tenant = user.tenant
        shop = self._get_shop()
        
        from apps.sales.models import Sale
        from apps.customers.models import CustomerTransaction
        from django.db.models import Sum, Q
        from datetime import datetime
        from decimal import Decimal
        from apps.accounting.models import DigitalFundWithdrawal

        sales = Sale.objects.filter(tenant=tenant, status='COMPLETED', payment_method='MOMO')
        cts = CustomerTransaction.objects.filter(tenant=tenant, transaction_type='CREDIT', description__icontains='MOMO')
        
        if shop:
            sales = sales.filter(shop=shop)
            cts = cts.filter(performed_by__location=shop)
            
        # Apply filters
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        min_amount = self.request.GET.get('min_amount')
        max_amount = self.request.GET.get('max_amount')
        
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                sales = sales.filter(created_at__date__gte=date_from_parsed)
                cts = cts.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass
                
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                sales = sales.filter(created_at__date__lte=date_to_parsed)
                cts = cts.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass
                
        if min_amount:
            try:
                min_val = Decimal(min_amount)
                sales = sales.filter(amount_paid__gte=min_val)
                cts = cts.filter(amount__gte=min_val)
            except (ValueError, TypeError):
                pass
                
        if max_amount:
            try:
                max_val = Decimal(max_amount)
                sales = sales.filter(amount_paid__lte=max_val)
                cts = cts.filter(amount__lte=max_val)
            except (ValueError, TypeError):
                pass
        
        # Balance calc (from all time ledger)
        if shop:
            total_sales = Sale.objects.filter(tenant=tenant, shop=shop, status='COMPLETED', payment_method='MOMO').aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            total_cts = CustomerTransaction.objects.filter(tenant=tenant, performed_by__location=shop, transaction_type='CREDIT', description__icontains='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
            total_withdrawn = DigitalFundWithdrawal.objects.filter(tenant=tenant, shop=shop, fund_source='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
        else:
            total_sales = Sale.objects.filter(tenant=tenant, status='COMPLETED', payment_method='MOMO').aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            total_cts = CustomerTransaction.objects.filter(tenant=tenant, transaction_type='CREDIT', description__icontains='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
            total_withdrawn = DigitalFundWithdrawal.objects.filter(tenant=tenant, fund_source='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        context['shop_balance'] = (total_sales + total_cts) - total_withdrawn
        context['shop'] = shop
        
        # Combine manually
        transactions = list(sales) + list(cts)
        
        # Apply Sorting
        sort_by = self.request.GET.get('sort', 'created_at')
        direction = self.request.GET.get('dir', 'desc')
        is_reverse = direction == 'desc'
        
        if sort_by == 'amount':
            transactions.sort(key=lambda x: getattr(x, 'amount_paid', getattr(x, 'amount', 0)), reverse=is_reverse)
        elif sort_by == 'type':
            transactions.sort(key=lambda x: 'Sale' if hasattr(x, 'sale_number') else 'Debt Payment', reverse=is_reverse)
        elif sort_by == 'status':
            transactions.sort(key=lambda x: x.is_accountant_confirmed, reverse=is_reverse)
        elif sort_by == 'reference':
            transactions.sort(key=lambda x: getattr(x, 'sale_number', getattr(x, 'customer', getattr(x, 'customer', ''))), reverse=is_reverse)
        else: # default created_at
            transactions.sort(key=lambda x: x.created_at, reverse=is_reverse)
        
        context['transactions'] = transactions
        
        # Pass filters back to context
        context['date_from'] = date_from
        context['date_to'] = date_to
        context['min_amount'] = min_amount
        context['max_amount'] = max_amount
        context['current_sort'] = sort_by
        context['current_dir'] = direction
        
        role_name = user.role.name if user.role else None
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            from apps.core.models import Location
            context['all_shops'] = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True).order_by('name')
            
        return context

class ShopMomoExportView(LoginRequiredMixin, View):
    """Export local momo history to Excel or PDF."""
    
    def dispatch(self, request, *args, **kwargs):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_MANAGER', 'SHOP_CASHIER']:
            messages.error(request, 'You do not have permission to export this report.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
        
    def get(self, request):
        from apps.sales.models import Sale
        from apps.customers.models import CustomerTransaction
        from apps.core.models import Location
        from datetime import datetime
        from apps.core.excel_utils import create_export_workbook, build_excel_response
        
        user = request.user
        tenant = user.tenant
        
        # Get shop
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
            return redirect('accounting:shop_momo_history')
            
        # Build querysets
        sales = Sale.objects.filter(tenant=tenant, shop=shop, status='COMPLETED', payment_method='MOMO')
        cts = CustomerTransaction.objects.filter(tenant=tenant, performed_by__location=shop, transaction_type='CREDIT', description__icontains='MOMO')
        
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
                sales = sales.filter(created_at__date__gte=date_from_parsed)
                cts = cts.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                sales = sales.filter(created_at__date__lte=date_to_parsed)
                cts = cts.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass
        if min_amount:
            try:
                sales = sales.filter(amount_paid__gte=Decimal(min_amount))
                cts = cts.filter(amount__gte=Decimal(min_amount))
            except (ValueError, TypeError):
                pass
        if max_amount:
            try:
                sales = sales.filter(amount_paid__lte=Decimal(max_amount))
                cts = cts.filter(amount__lte=Decimal(max_amount))
            except (ValueError, TypeError):
                pass
                
        transactions = list(sales) + list(cts)
        
        # Sort
        sort_by = request.GET.get('sort', 'created_at')
        direction = request.GET.get('dir', 'desc')
        is_reverse = direction == 'desc'
        
        if sort_by == 'amount':
            transactions.sort(key=lambda x: getattr(x, 'amount_paid', getattr(x, 'amount', 0)), reverse=is_reverse)
        elif sort_by == 'type':
            transactions.sort(key=lambda x: 'Sale' if hasattr(x, 'sale_number') else 'Debt Payment', reverse=is_reverse)
        elif sort_by == 'status':
            transactions.sort(key=lambda x: x.is_accountant_confirmed, reverse=is_reverse)
        elif sort_by == 'reference':
            transactions.sort(key=lambda x: getattr(x, 'sale_number', getattr(x, 'customer', getattr(x, 'customer', ''))), reverse=is_reverse)
        else: # default created_at
            transactions.sort(key=lambda x: x.created_at, reverse=is_reverse)
            
        headers = ['Date & Time', 'Type', 'Shop', 'Reference / Customer', 'Status', 'Amount']
        rows = []
        for t in transactions:
            is_sale = hasattr(t, 'sale_number')
            t_type = 'Sale' if is_sale else 'Debt Payment'
            ref = t.sale_number if is_sale else str(t.customer)
            amount = float(t.amount_paid) if is_sale else float(t.amount)
            status = "Confirmed" if t.is_accountant_confirmed else "Pending"
            
            rows.append([
                t.created_at.strftime('%Y-%m-%d %H:%M'),
                t_type,
                shop.name,
                ref,
                status,
                amount
            ])
            
        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop.name,
                'date_range': date_range_str
            }
            return export_to_pdf('local_momo_history.pdf', 'Local Momo History', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Momo History', headers, rows)
            return build_excel_response(wb, 'local_momo_history.xlsx')


# --- Expenditure Views (ported from alpha) ---

class ApiAddExpenditureCategoryView(LoginRequiredMixin, View):
    """API endpoint to add a new expenditure category."""
    
    def post(self, request):
        from django.http import JsonResponse
        if not request.user.role or request.user.role.name != 'ADMIN':
            return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)
            
        import json
        name = request.POST.get('name', '').strip()
        if not name:
            try:
                data = json.loads(request.body)
                name = data.get('name', '').strip()
            except:
                pass
                
        if not name:
            return JsonResponse({'success': False, 'error': 'Category name is required'})
            
        from .models import ExpenditureCategory
        try:
            cat, created = ExpenditureCategory.objects.get_or_create(
                tenant=request.user.tenant,
                name=name,
                defaults={'is_active': True}
            )
            if not created and not cat.is_active:
                cat.is_active = True
                cat.save()
            elif not created:
                return JsonResponse({'success': False, 'error': 'Category already exists'})
                
            return JsonResponse({
                'success': True, 
                'category': {'id': cat.id, 'name': cat.name, 'is_default': cat.is_default}
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


class ApiDeleteExpenditureCategoryView(LoginRequiredMixin, View):
    """API endpoint to soft-delete an expenditure category."""
    
    def post(self, request, pk):
        from django.http import JsonResponse
        if not request.user.role or request.user.role.name != 'ADMIN':
            return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)
            
        from .models import ExpenditureCategory
        try:
            cat = ExpenditureCategory.objects.get(pk=pk, tenant=request.user.tenant)
            if cat.is_default:
                return JsonResponse({'success': False, 'error': 'Cannot delete a default category'})
                
            cat.is_active = False
            cat.save()
            return JsonResponse({'success': True})
        except ExpenditureCategory.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Category not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


class ExpenditureListView(LoginRequiredMixin, ListView):
    """List expenditure vouchers; filterable by status, date and category."""
    model = ExpenditureRequest
    template_name = 'accounting/expenditure_list.html'
    context_object_name = 'vouchers'
    paginate_by = 20

    def get_queryset(self):
        role_name = self.request.user.role.name if self.request.user.role else None
        qs = ExpenditureRequest.objects.select_related(
            'requested_by', 'location'
        ).prefetch_related('items').filter(tenant=self.request.user.tenant)

        # Shop-level roles only see their own location's vouchers
        # Accountant, Auditor, and Admin see all shops
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER', 'SHOP_ATTENDANT']:
            if self.request.user.location:
                qs = qs.filter(location=self.request.user.location)

        # Apply filters from GET params
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)

        category = self.request.GET.get('category')
        if category:
            qs = qs.filter(items__category_id=category).distinct()

        date_from = self.request.GET.get('date_from', '')
        date_to = self.request.GET.get('date_to', '')
        if date_from:
            try:
                qs = qs.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                pass
        if date_to:
            try:
                qs = qs.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                pass

        # Accountants and Auditors see pending vouchers first
        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            from django.db.models import Case, When, IntegerField
            qs = qs.annotate(
                status_order=Case(
                    When(status='PENDING', then=0),
                    When(status='PARTIAL', then=1),
                    When(status='FULLY_APPROVED', then=2),
                    When(status='REJECTED', then=3),
                    default=4,
                    output_field=IntegerField(),
                )
            ).order_by('status_order', '-created_at')
        else:
            qs = qs.order_by('-created_at')

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        role_name = self.request.user.role.name if self.request.user.role else None
        ctx['is_accountant'] = role_name in ['ACCOUNTANT', 'ADMIN']
        ctx['is_auditor'] = role_name == 'AUDITOR'
        ctx['is_shop_manager'] = role_name == 'SHOP_MANAGER'
        # Auditors share the same broad view as accountants but cannot approve
        ctx['can_view_all_shops'] = role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']
        ctx['selected_status'] = self.request.GET.get('status', '')
        ctx['selected_category'] = self.request.GET.get('category', '')
        ctx['date_from'] = self.request.GET.get('date_from', '')
        ctx['date_to'] = self.request.GET.get('date_to', '')

        # Categories for filter dropdown
        ctx['categories'] = ExpenditureCategory.objects.filter(
            tenant=self.request.user.tenant
        ).order_by('name')

        # Aggregate totals across the filtered queryset
        full_qs = self.get_queryset()
        item_agg = ExpenditureItem.objects.filter(request__in=full_qs).aggregate(
            total_requested=Sum('amount'),
            total_approved=Sum('amount', filter=Q(status='APPROVED')),
            total_pending=Sum('amount', filter=Q(status='PENDING')),
            total_rejected=Sum('amount', filter=Q(status='REJECTED')),
        )
        ctx['exp_total_requested'] = item_agg['total_requested'] or Decimal('0')
        ctx['exp_total_approved'] = item_agg['total_approved'] or Decimal('0')
        ctx['exp_total_pending'] = item_agg['total_pending'] or Decimal('0')
        ctx['exp_total_rejected'] = item_agg['total_rejected'] or Decimal('0')
        ctx['voucher_count'] = full_qs.count()

        # Count of vouchers awaiting accountant action (for badge)
        ctx['pending_count'] = ExpenditureRequest.objects.filter(
            tenant=self.request.user.tenant,
            status__in=['PENDING', 'PARTIAL']
        ).count()

        # Category breakdown for accountant/auditor summary
        if ctx['is_accountant'] or ctx['is_auditor']:
            from django.db.models import Count
            ctx['category_breakdown'] = ExpenditureItem.objects.filter(
                request__in=full_qs,
                status='APPROVED'
            ).values('category__name').annotate(
                total=Sum('amount'), count=Count('id')
            ).order_by('-total')

        return ctx


class ExpenditureCreateView(LoginRequiredMixin, View):
    """Create a new expenditure voucher (Shop Manager / Admin only)."""
    template_name = 'accounting/expenditure_form.html'
    success_url = reverse_lazy('accounting:expenditure_list')

    def _check_access(self, request):
        """Return an error redirect if the user isn't allowed to create expenditures."""
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['SHOP_MANAGER', 'ADMIN']:
            messages.error(request, "Only Shop Managers can submit expenditure vouchers.")
            return redirect('accounting:expenditure_list')
        if not request.user.location:
            messages.error(request, "You must be assigned to a shop location to create an expenditure.")
            return redirect('accounting:expenditure_list')
        return None

    def get(self, request):
        redirect_response = self._check_access(request)
        if redirect_response:
            return redirect_response

        form = ExpenditureRequestForm()
        formset = ExpenditureItemFormSet(form_kwargs={'tenant': request.user.tenant})
        return render(request, self.template_name, {'form': form, 'formset': formset})

    def post(self, request):
        redirect_response = self._check_access(request)
        if redirect_response:
            return redirect_response

        form = ExpenditureRequestForm(request.POST)
        formset = ExpenditureItemFormSet(request.POST, form_kwargs={'tenant': request.user.tenant})

        if form.is_valid() and formset.is_valid():
            voucher = form.save(commit=False)
            voucher.tenant = request.user.tenant
            voucher.requested_by = request.user
            voucher.location = request.user.location
            voucher.save()

            items = formset.save(commit=False)
            for item in items:
                item.tenant = request.user.tenant
                item.request = voucher
                item.save()
            formset.save_m2m()

            # --- Notify all accountants that a new voucher needs review ---
            from apps.notifications.models import Notification
            from apps.core.models import User as CoreUser
            accountants = CoreUser.objects.filter(
                tenant=request.user.tenant,
                is_active=True,
                role__name__in=['ACCOUNTANT', 'ADMIN'],
            )
            item_count = voucher.items.count()
            for accountant in accountants:
                Notification.objects.create(
                    tenant=request.user.tenant,
                    user=accountant,
                    title="New Expenditure Voucher Submitted",
                    message=(
                        f"{request.user.get_full_name()} ({voucher.location.name}) "
                        f"submitted expenditure voucher {voucher.voucher_number} "
                        f"with {item_count} item{'s' if item_count != 1 else ''} "
                        f"totalling {request.user.tenant.currency} {voucher.total_amount}. "
                        f"Please review and approve."
                    ),
                    notification_type='SYSTEM',
                    reference_type='ExpenditureRequest',
                    reference_id=voucher.pk,
                )

            messages.success(request, f"Expenditure voucher {voucher.voucher_number} submitted for approval.")
            return redirect(self.success_url)

        return render(request, self.template_name, {'form': form, 'formset': formset})


class ExpenditureDetailView(LoginRequiredMixin, DetailView):
    """View details of an expenditure voucher, including its items."""
    model = ExpenditureRequest
    template_name = 'accounting/expenditure_detail.html'
    context_object_name = 'voucher'

    def get_queryset(self):
        return ExpenditureRequest.objects.filter(tenant=self.request.user.tenant)


class ExpenditureItemActionView(LoginRequiredMixin, View):
    """Approve or Reject an individual expenditure item (Accountant / Admin only)."""

    def post(self, request, pk, action):
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, 'Only accountants or admins can process expenditures.')
            return redirect('accounting:expenditure_list')

        item = get_object_or_404(ExpenditureItem, pk=pk, tenant=request.user.tenant)

        if item.status != 'PENDING':
            messages.error(request, 'This item is already processed.')
            return redirect('accounting:expenditure_detail', pk=item.request.pk)

        try:
            if action == 'approve':
                source_of_funds = request.POST.get('source_of_funds')
                item.approve(request.user, source_of_funds)
                messages.success(request, f"Item approved.")
            elif action == 'reject':
                reason = request.POST.get('reason', '')
                item.reject(request.user, reason)
                messages.success(request, f"Item rejected.")
        except ValidationError as e:
            messages.error(request, str(e))
            
        return redirect('accounting:expenditure_detail', pk=item.request.pk)



class ExpenditureCategoryView(LoginRequiredMixin, View):
    """Manage expenditure categories (Admin / Accountant only)."""
    template_name = 'accounting/expenditure_categories.html'

    def _check_permission(self, request):
        role_name = request.user.role.name if request.user.role else None
        return role_name in ['ADMIN', 'ACCOUNTANT']

    def get(self, request):
        if not self._check_permission(request):
            messages.error(request, 'Permission denied.')
            return redirect('accounting:expenditure_list')
        categories = ExpenditureCategory.objects.filter(tenant=request.user.tenant)
        form = ExpenditureCategoryForm()
        return render(request, self.template_name, {'categories': categories, 'form': form})

    def post(self, request):
        if not self._check_permission(request):
            messages.error(request, 'Permission denied.')
            return redirect('accounting:expenditure_list')

        action = request.POST.get('action')
        if action == 'create':
            form = ExpenditureCategoryForm(request.POST)
            if form.is_valid():
                cat = form.save(commit=False)
                cat.tenant = request.user.tenant
                cat.save()
                messages.success(request, f'Category "{cat.name}" created.')
            else:
                messages.error(request, 'Invalid category name.')
        elif action == 'toggle':
            cat = get_object_or_404(ExpenditureCategory, pk=request.POST.get('pk'), tenant=request.user.tenant)
            if cat.is_default and cat.is_active:
                messages.warning(request, 'Default categories cannot be deactivated.')
            else:
                cat.is_active = not cat.is_active
                cat.save()
                messages.success(request, f'Category "{cat.name}" updated.')
        elif action == 'delete':
            cat = get_object_or_404(ExpenditureCategory, pk=request.POST.get('pk'), tenant=request.user.tenant)
            if cat.is_default:
                messages.warning(request, 'Default categories cannot be deleted, but you can deactivate them.')
            elif cat.items.exists():
                messages.warning(request, f'Cannot delete "{cat.name}" — it has existing expenditures. Deactivate it instead.')
            else:
                cat.delete()
                messages.success(request, f'Category deleted.')
        return redirect('accounting:expenditure_categories')


class ExpenditureReportView(LoginRequiredMixin, View):
    """Expenditure report grouped by category and date range."""
    template_name = 'accounting/expenditure_report.html'
    ALLOWED_ROLES = ['CASHIER', 'SHOP_MANAGER', 'SHOP_CASHIER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN']

    def get(self, request):
        from datetime import datetime
        from django.db.models import Sum, Count
        from decimal import Decimal
        role_name = request.user.role.name if request.user.role else None
        if role_name not in self.ALLOWED_ROLES:
            messages.error(request, 'You do not have permission to view expenditure reports.')
            return redirect('core:dashboard')

        tenant = request.user.tenant
        date_from_str = request.GET.get('date_from', '')
        date_to_str = request.GET.get('date_to', '')
        location_id = request.GET.get('location', '')

        # Default: current month
        today = timezone.localdate()
        if date_from_str:
            try:
                date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
            except ValueError:
                date_from = today.replace(day=1)
        else:
            date_from = today.replace(day=1)

        if date_to_str:
            try:
                date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
            except ValueError:
                date_to = today
        else:
            date_to = today

        qs = ExpenditureItem.objects.select_related('request').filter(
            tenant=tenant,
            status='APPROVED',
            request__created_at__date__gte=date_from,
            request__created_at__date__lte=date_to,
        )

        # Shop-level roles see only their location
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and request.user.location:
            qs = qs.filter(request__location=request.user.location)
        elif location_id:
            qs = qs.filter(request__location_id=location_id)

        # Aggregate by category
        by_category = (
            qs.values('category__name')
            .annotate(total=Sum('amount'), count=Count('id'))
            .order_by('-total')
        )

        grand_total = qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')

        locations = Location.objects.filter(tenant=tenant, is_active=True) if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN'] else []

        return render(request, self.template_name, {
            'by_category': by_category,
            'grand_total': grand_total,
            'date_from': date_from,
            'date_to': date_to,
            'location_id': location_id,
            'locations': locations,
            'expenditures': qs.select_related('category', 'request__location', 'request__requested_by').order_by('-request__created_at'),
        })

class CashTransferPrintView(LoginRequiredMixin, View):
    """Printable view for a cash transfer deposit slip."""
    template_name = 'accounting/cash_transfer_print.html'
    
    def get(self, request, pk):
        transfer = get_object_or_404(
            CashTransfer,
            pk=pk,
            tenant=request.user.tenant
        )
        return render(request, self.template_name, {'transfer': transfer})


class CashHistoryView(LoginRequiredMixin, ListView):
    """
    Cash history page linked from the Cash-on-Hand navbar badge.
    Shows cash sales, cash customer payments and cash transfers
    for the current user's shop with date filtering and totals.
    """
    template_name = 'accounting/cash_history.html'
    context_object_name = 'cash_sales'
    paginate_by = 30

    def get_queryset(self):
        from apps.sales.models import Sale
        user = self.request.user
        role_name = user.role.name if user.role else None
        qs = Sale.objects.filter(
            tenant=user.tenant,
            status='COMPLETED',
            payment_method__in=['CASH', 'MIXED'],
        ).select_related('attendant', 'shop')

        # Scope to user's shop unless accountant/admin
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER', 'SHOP_ATTENDANT']:
            if user.location:
                qs = qs.filter(shop=user.location)
            else:
                qs = qs.filter(attendant=user)

        date_from = self.request.GET.get('date_from', '')
        date_to = self.request.GET.get('date_to', '')
        if date_from:
            try:
                qs = qs.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                pass
        if date_to:
            try:
                qs = qs.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                pass
                
        shop_id = self.request.GET.get('shop')
        if shop_id and role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            qs = qs.filter(shop_id=shop_id)

        # Sorting
        sort = self.request.GET.get('sort', 'date_desc')
        sort_map = {
            'date_asc': 'created_at',
            'date_desc': '-created_at',
            'amount_asc': 'total',
            'amount_desc': '-total',
            'attendant_asc': 'attendant__first_name',
            'attendant_desc': '-attendant__first_name',
        }
        qs = qs.order_by(sort_map.get(sort, '-created_at'))
        return qs

    def get_context_data(self, **kwargs):
        from apps.sales.models import Sale
        from apps.customers.models import CustomerTransaction
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role_name = user.role.name if user.role else None

        date_from = self.request.GET.get('date_from', '')
        date_to = self.request.GET.get('date_to', '')
        sort = self.request.GET.get('sort', 'date_desc')
        shop_id = self.request.GET.get('shop', '')
        context['date_from'] = date_from
        context['date_to'] = date_to
        context['sort'] = sort
        context['selected_shop'] = shop_id
        
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            from apps.core.models import Location
            context['shops'] = Location.objects.filter(tenant=user.tenant, location_type='SHOP', is_active=True)
            context['is_full_view'] = True

        # ---- Filtered queryset totals ----
        qs = self.get_queryset()
        agg = qs.aggregate(
            total_cash=Sum('total', filter=Q(payment_method='CASH')),
            total_mixed=Sum('amount_paid', filter=Q(payment_method='MIXED')),
            sale_count=Count('id'),
        )
        total_cash = (agg['total_cash'] or Decimal('0')) + (agg['total_mixed'] or Decimal('0'))
        context['total_cash_sales'] = total_cash
        context['sale_count'] = agg['sale_count'] or 0

        # ---- Cash transfers (outgoing deposits) ----
        transfer_qs = CashTransfer.objects.filter(
            tenant=user.tenant,
        ).order_by('-created_at')
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER', 'SHOP_ATTENDANT']:
            transfer_qs = transfer_qs.filter(Q(from_user=user) | Q(to_user=user))
        if date_from:
            try:
                transfer_qs = transfer_qs.filter(
                    created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date()
                )
            except ValueError:
                pass
        if date_to:
            try:
                transfer_qs = transfer_qs.filter(
                    created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date()
                )
            except ValueError:
                pass
        context['cash_transfers'] = transfer_qs[:20]
        context['total_transferred'] = transfer_qs.filter(
            status='CONFIRMED', transfer_type='DEPOSIT'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # ---- Cash customer payments ----
        cust_qs = CustomerTransaction.objects.filter(
            tenant=user.tenant,
            transaction_type='CREDIT',
            description__icontains='(CASH)',
        ).exclude(description__icontains='ECASH').order_by('-created_at')
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER', 'SHOP_ATTENDANT']:
            cust_qs = cust_qs.filter(performed_by=user)
        if date_from:
            try:
                cust_qs = cust_qs.filter(
                    created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date()
                )
            except ValueError:
                pass
        if date_to:
            try:
                cust_qs = cust_qs.filter(
                    created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date()
                )
            except ValueError:
                pass
        context['cash_customer_payments'] = cust_qs[:20]
        context['total_customer_cash'] = cust_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # ---- Permissions ----
        if getattr(user.tenant, 'use_strict_sales_workflow', False):
            context['can_create_transfer'] = role_name in ['SHOP_CASHIER', 'ACCOUNTANT', 'ADMIN']
        else:
            context['can_create_transfer'] = role_name in ['SHOP_ATTENDANT', 'SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN', 'SHOP_CASHIER']

        context['role_name'] = role_name
        return context

class CashHistoryExportView(LoginRequiredMixin, View):
    """
    Export cash sales history to Excel or PDF.
    Mirrors the queryset logic of CashHistoryView.
    """
    def get(self, request):
        from apps.sales.models import Sale
        from apps.core.excel_utils import create_export_workbook, build_excel_response
        
        user = request.user
        role_name = user.role.name if user.role else None
        
        qs = Sale.objects.filter(
            tenant=user.tenant,
            status='COMPLETED',
            payment_method__in=['CASH', 'MIXED'],
        ).select_related('attendant', 'shop')

        # Scope to user's shop unless accountant/admin
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER', 'SHOP_ATTENDANT']:
            if user.location:
                qs = qs.filter(shop=user.location)
            else:
                qs = qs.filter(attendant=user)

        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        if date_from:
            try:
                from datetime import datetime
                qs = qs.filter(created_at__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
            except ValueError:
                pass
                
        shop_id = request.GET.get('shop')
        if shop_id and role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            qs = qs.filter(shop_id=shop_id)
        if date_to:
            try:
                from datetime import datetime
                qs = qs.filter(created_at__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
            except ValueError:
                pass

        qs = qs.order_by('-created_at')

        headers = ['Date & Time', 'Invoice Number', 'Attendant', 'Shop', 'Payment Method', 'Amount']
        rows = []
        for sale in qs:
            amount = float(sale.amount_paid) if sale.payment_method == 'MIXED' else float(sale.total)
            method = "Mixed" if sale.payment_method == 'MIXED' else "Cash"
            rows.append([
                sale.created_at.strftime("%Y-%m-%d %H:%M"),
                sale.sale_number,
                sale.attendant.get_full_name() or sale.attendant.email,
                sale.shop.name if sale.shop else '-',
                method,
                amount
            ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            shop_name = "All Shops"
            if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
                shop_name = user.location.name if user.location else ''
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop_name,
                'date_range': f"{date_from or 'All Time'} to {date_to or 'All Time'}"
            }
            return export_to_pdf('cash_sales_history.pdf', 'Cash Sales History', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Cash Sales', headers, rows)
            return build_excel_response(wb, 'cash_sales_history.xlsx')
from apps.accounting.models import BankTransfer
from apps.core.excel_utils import create_export_workbook, build_excel_response
from apps.core.pdf_utils import export_to_pdf
from django.db.models import Sum
from datetime import datetime

class BankHistoryListView(LoginRequiredMixin, SortableMixin, ListView):
    """
    Bank history page.
    Shows bank transfers with date filtering, fund source filtering, and totals.
    """
    template_name = 'accounting/bank_history.html'
    context_object_name = 'bank_transfers'
    paginate_by = 30
    model = BankTransfer

    def get_queryset(self):
        user = self.request.user
        role_name = user.role.name if user.role else None
        
        qs = BankTransfer.objects.filter(tenant=user.tenant)
        if role_name not in ['SUPER_ADMIN', 'ADMIN', 'AUDITOR', 'ACCOUNTANT']:
            qs = qs.filter(accountant=user)
            
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        fund_source = self.request.GET.get('fund_source')
        provider_config_id = self.request.GET.get('provider_config')
        filter_user_id = self.request.GET.get('user')
        filter_shop_id = self.request.GET.get('shop')
        
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if fund_source:
            qs = qs.filter(fund_source=fund_source)
            if fund_source == 'ECASH' and provider_config_id:
                qs = qs.filter(provider_config_id=provider_config_id)
                
        if role_name in ['SUPER_ADMIN', 'ADMIN', 'AUDITOR', 'ACCOUNTANT']:
            if filter_user_id:
                qs = qs.filter(accountant_id=filter_user_id)
            if filter_shop_id:
                qs = qs.filter(accountant__location_id=filter_shop_id)
            
        return qs.order_by(*self.get_ordering())
        
    def get_ordering(self):
        sort_by = self.request.GET.get('sort', '-created_at')
        return [sort_by]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['selected_fund_source'] = self.request.GET.get('fund_source', '')
        context['selected_provider_config'] = self.request.GET.get('provider_config', '')
        
        from apps.payments.models import PaymentProviderConfig
        context['providers'] = PaymentProviderConfig.objects.filter(
            tenant=self.request.user.tenant, 
            is_active=True
        ).order_by('nickname')
        
        user = self.request.user
        role_name = user.role.name if user.role else None
        
        if role_name in ['SUPER_ADMIN', 'ADMIN', 'AUDITOR', 'ACCOUNTANT']:
            from apps.core.models import Location, User
            context['is_full_view'] = True
            context['shops'] = Location.objects.filter(tenant=user.tenant, location_type='SHOP', is_active=True).order_by('name')
            context['users'] = User.objects.filter(
                tenant=user.tenant, 
                role__name__in=['SHOP_CASHIER', 'ACCOUNTANT', 'ADMIN'], 
                is_active=True
            ).order_by('first_name', 'email')
            context['selected_shop'] = self.request.GET.get('shop', '')
            context['selected_user'] = self.request.GET.get('user', '')
        
        context['total_deposited'] = qs.aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        context['fund_choices'] = BankTransfer.FUND_CHOICES
        return context

class BankHistoryExportView(LoginRequiredMixin, View):
    """
    Export bank transfers history to Excel or PDF.
    """
    def get(self, request):
        user = request.user
        role_name = user.role.name if user.role else None
        
        qs = BankTransfer.objects.filter(tenant=user.tenant)
        if role_name not in ['SUPER_ADMIN', 'ADMIN', 'AUDITOR', 'ACCOUNTANT']:
            qs = qs.filter(accountant=user)
            
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
        fund_source = request.GET.get('fund_source')
        provider_config_id = request.GET.get('provider_config')
        filter_user_id = request.GET.get('user')
        filter_shop_id = request.GET.get('shop')
        
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if fund_source:
            qs = qs.filter(fund_source=fund_source)
            if fund_source == 'ECASH' and provider_config_id:
                qs = qs.filter(provider_config_id=provider_config_id)
                
        if role_name in ['SUPER_ADMIN', 'ADMIN', 'AUDITOR', 'ACCOUNTANT']:
            if filter_user_id:
                qs = qs.filter(accountant_id=filter_user_id)
            if filter_shop_id:
                qs = qs.filter(accountant__location_id=filter_shop_id)
            
        sort_by = request.GET.get('sort', '-created_at')
        qs = qs.order_by(sort_by)
        
        export_format = request.GET.get('format', 'excel')
        headers = ['Date', 'Accountant', 'Fund Source', 'Platform', 'Teller Name', 'Notes', 'Amount']
        rows = []
        for bt in qs:
            provider = bt.provider_config.provider.name if bt.provider_config else ''
            rows.append([
                bt.created_at.strftime('%Y-%m-%d %H:%M'),
                bt.accountant.get_full_name() or bt.accountant.email,
                bt.get_fund_source_display(),
                provider,
                bt.teller_name,
                bt.notes,
                float(bt.amount)
            ])
            
        if export_format == 'pdf':
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'date_range': f"{date_from or 'All Time'} to {date_to or 'All Time'}"
            }
            return export_to_pdf('bank_history.pdf', 'Bank History', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Bank History', headers, rows)
            return build_excel_response(wb, 'bank_history.xlsx')
