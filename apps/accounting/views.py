"""
Views for the accounting app.
Handles cash transfers between shop managers and accountants.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.views import View
from django.views.generic import ListView
from django.db.models import Q, Sum, Count
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from .models import CashTransfer
from .forms import CashTransferForm
from apps.core.models import User, Location


class CashTransferListView(LoginRequiredMixin, ListView):
    """List cash transfers for the current user."""
    model = CashTransfer
    template_name = 'accounting/cash_transfer_list.html'
    context_object_name = 'transfers'
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
        
        return queryset
    
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
        context['can_create'] = role_name in ['SHOP_ATTENDANT', 'SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN']
        
        # For accountants/auditors/admin: show filters
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            context['is_full_view'] = True
            context['shops'] = Location.objects.filter(
                tenant=user.tenant,
                location_type='SHOP',
                is_active=True
            )
            context['can_send_to_shops'] = user.tenant.allow_accountant_to_shop_transfers if user.tenant else False
            
            # Preserve filter values
            context['date_from'] = self.request.GET.get('date_from', '')
            context['date_to'] = self.request.GET.get('date_to', '')
            context['selected_shop'] = self.request.GET.get('shop', '')
            context['selected_status'] = self.request.GET.get('status', '')
            
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
        
        return context


class CashTransferCreateView(LoginRequiredMixin, View):
    """Create a new cash transfer."""
    template_name = 'accounting/cash_transfer_form.html'
    
    def dispatch(self, request, *args, **kwargs):
        # Shop Attendants, Shop Managers, Accountants and Admins can create transfers
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['SHOP_ATTENDANT', 'SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN']:
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
            
            # Set transfer type based on sender's role
            role_name = request.user.role.name if request.user.role else None
            if role_name == 'ACCOUNTANT':
                transfer.transfer_type = 'FLOAT'
            else:
                transfer.transfer_type = 'DEPOSIT'
            
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
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['ACCOUNTANT', 'ADMIN']:
            messages.error(request, 'Only accountants can access this dashboard.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)
    
    def get(self, request):
        from apps.sales.models import Sale
        
        user = request.user
        tenant = user.tenant
        
        # Date range filter
        date_range = request.GET.get('range', 'today')
        today = timezone.now().date()
        
        if date_range == 'week':
            start_date = today - timedelta(days=7)
        elif date_range == 'month':
            start_date = today - timedelta(days=30)
        else:
            start_date = today
        
        context = {
            'current_range': date_range,
        }
        
        # ===== SALES SUMMARY =====
        sales_filter = Q(
            tenant=tenant,
            status='COMPLETED',
            created_at__date__gte=start_date
        )
        
        context['sales_summary'] = Sale.objects.filter(sales_filter).aggregate(
            total_revenue=Sum('total'),
            total_count=Count('id'),
            cash_total=Sum('total', filter=Q(payment_method='CASH')),
            ecash_total=Sum('total', filter=Q(payment_method='ECASH')),
        )
        
        # Sales by shop
        context['sales_by_shop'] = Sale.objects.filter(sales_filter).values(
            'shop__id', 'shop__name'
        ).annotate(
            revenue=Sum('total'),
            count=Count('id')
        ).order_by('-revenue')
        
        # ===== CASH DEPOSITS =====
        deposit_filter = Q(
            tenant=tenant,
            transfer_type='DEPOSIT',
            created_at__date__gte=start_date
        )
        
        context['deposits_summary'] = CashTransfer.objects.filter(deposit_filter).aggregate(
            pending_amount=Sum('amount', filter=Q(status='PENDING')),
            pending_count=Count('id', filter=Q(status='PENDING')),
            confirmed_amount=Sum('amount', filter=Q(status='CONFIRMED')),
            confirmed_count=Count('id', filter=Q(status='CONFIRMED')),
        )
        
        # Deposits by shop
        context['deposits_by_shop'] = CashTransfer.objects.filter(
            deposit_filter,
            status='CONFIRMED'
        ).values(
            'from_location__id', 'from_location__name'
        ).annotate(
            total=Sum('amount'),
            count=Count('id')
        ).order_by('-total')
        
        # Recent confirmed deposits
        context['recent_deposits'] = CashTransfer.objects.filter(
            tenant=tenant,
            transfer_type='DEPOSIT',
            status='CONFIRMED'
        ).select_related('from_user', 'from_location').order_by('-confirmed_at')[:10]
        
        # Pending deposits awaiting confirmation
        context['pending_deposits'] = CashTransfer.objects.filter(
            tenant=tenant,
            transfer_type='DEPOSIT',
            status='PENDING',
            to_user=user
        ).select_related('from_user', 'from_location').order_by('-created_at')
        
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
            status='COMPLETED',
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
            'attendants': CoreUser.objects.filter(tenant=tenant, role__name='SHOP_ATTENDANT', is_active=True),
        }
        
        return render(request, self.template_name, context)


class PriceHistoryView(LoginRequiredMixin, View):
    """
    Audit trail of all shop price changes.
    """
    template_name = 'accounting/price_history.html'
    
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
        
        context = {
            'prices': prices[:100],  # Limit for performance
            'total_count': prices.count(),
            'shops': Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True),
            'products': Product.objects.filter(tenant=tenant, is_active=True)[:50],
        }
        
        return render(request, self.template_name, context)
