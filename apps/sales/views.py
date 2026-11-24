"""
Views for the sales app.
Includes POS interface and API endpoints for cart operations.
"""
import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, DetailView, View, UpdateView
from django.urls import reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.db import models, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import Sale, SaleItem, Shift, ShopSettings
from apps.inventory.models import Product, ShopPrice, Batch
from apps.core.models import Location
from apps.core.mixins import PaginationMixin, SortableMixin
from apps.core.decorators import AdminOrManagerRequiredMixin, AdminRequiredMixin
from .forms import ShopManagerSettingsForm, AdminShopPaymentSettingsForm


class POSView(LoginRequiredMixin, View):
    """Main POS interface."""
    template_name = 'sales/pos.html'
    
    def get(self, request):
        from django.db.models import Sum, Prefetch, F, Q
        from apps.core.models import Location
        from apps.inventory.models import InventoryLedger, Category
        from apps.customers.models import Customer
        
        # Get user's shop location
        user_shop = request.user.location
        role_name = request.user.role.name if request.user.role else ''
        is_hq_or_admin = bool(request.user.is_superuser or role_name in ['ADMIN', 'ACCOUNTANT', 'AUDITOR'])
        shop_param = request.GET.get('shop')

        if is_hq_or_admin:
            if shop_param:
                explicit_shop = Location.objects.filter(
                    tenant=request.user.tenant,
                    location_type='SHOP',
                    pk=shop_param,
                    is_active=True
                ).first()
                if explicit_shop:
                    user_shop = explicit_shop
            elif not user_shop or user_shop.location_type != 'SHOP':
                user_shop = Location.objects.filter(
                    tenant=request.user.tenant,
                    location_type='SHOP',
                    is_active=True
                ).order_by('name').first()
        
        if not user_shop or user_shop.location_type != 'SHOP':
            messages.error(request, "You must be assigned to a shop to use the POS.")
            return redirect('core:dashboard')
        
        # Get or create shop settings
        shop_settings, _ = ShopSettings.objects.get_or_create(
            tenant=request.user.tenant,
            shop=user_shop,
            defaults={'receipt_printer_type': 'THERMAL_80MM'}
        )
        
        # Get open shift or prompt to open one
        open_shift = Shift.objects.filter(
            tenant=request.user.tenant,
            shop=user_shop,
            attendant=request.user,
            status='OPEN'
        ).first()
        
        # Pre-fetch stock quantities for all products at this location in ONE query
        stock_by_product = dict(
            InventoryLedger.objects.filter(
                tenant=request.user.tenant,
                location=user_shop
            ).values('product_id').annotate(
                total_stock=Sum('quantity')
            ).values_list('product_id', 'total_stock')
        )

        # In strict sales workflow, deduct undispatched items on active sales to compute net available stock,
        # unless 'show_items_with_physical_stock_in_pos' is enabled for this shop.
        if request.user.tenant.use_strict_sales_workflow and not shop_settings.show_items_with_physical_stock_in_pos:
            pending_by_product = dict(
                SaleItem.objects.filter(
                    sale__tenant=request.user.tenant,
                    sale__shop=user_shop,
                    sale__status__in=['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED', 'COMPLETED'],
                    dispatched_quantity__lt=F('quantity')
                ).exclude(
                    Q(sale__status__in=['VOIDED', 'REFUNDED']) | Q(sale__is_halted=True)
                ).values('product_id').annotate(
                    total_pending=Sum(F('quantity') - F('dispatched_quantity'))
                ).values_list('product_id', 'total_pending')
            )
            for pid, pnd in pending_by_product.items():
                cur = stock_by_product.get(pid, 0) or 0
                net = float(cur) - float(pnd)
                if not request.user.tenant.allow_negative_stock:
                    net = max(0.0, net)
                stock_by_product[pid] = net
        
        # Get products with prefetched shop prices for THIS shop only
        shop_price_prefetch = Prefetch(
            'shop_prices',
            queryset=ShopPrice.objects.filter(location=user_shop, is_active=True),
            to_attr='current_shop_prices'
        )
        
        products = Product.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        ).select_related('category').prefetch_related(shop_price_prefetch)
        
        # Build product list efficiently - no more N+1 queries
        products_with_prices = []
        for product in products:
            # Get shop price from prefetched list
            shop_prices = getattr(product, 'current_shop_prices', [])
            if shop_prices:
                shop_price = shop_prices[0]
                # Get quantity from pre-fetched dict
                quantity = stock_by_product.get(product.pk, 0) or 0
                
                if shop_settings.hide_zero_stock_in_pos and float(quantity) <= 0:
                    continue
                
                products_with_prices.append({
                    'id': product.pk,
                    'name': product.name,
                    'sku': product.sku or '',
                    'category': product.category.name if product.category else 'Uncategorized',
                    'price': str(shop_price.selling_price),
                    'unit': product.unit_of_measure,
                    'quantity': float(quantity),
                    'threshold': float(product.reorder_level),
                    'image': product.image.url if getattr(product, 'image', None) and product.image.name else '',
                })
        
        # Get categories for filtering
        categories = Category.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        )

        # Get customers for POS search (only essential fields)
        customers = Customer.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        ).values('id', 'name', 'phone', 'current_balance', 'credit_limit')
        
        # Get active payment providers for this shop
        from apps.payments.models import ShopPaymentAssignment, PaymentProviderConfig
        
        # First get shop-specific assignments
        assignments = ShopPaymentAssignment.objects.filter(
            shop=user_shop,
            provider_config__is_active=True
        ).select_related('provider_config').order_by('priority')
        
        available_providers = []
        if assignments.exists():
            for assignment in assignments:
                config = assignment.provider_config
                provider_cls = None
                
                # Instantiating the provider to get the checkout type
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
                        'checkout_type': provider_cls.get_checkout_type,
                        'is_default': assignment.is_default
                    })
        else:
            # Fallback to any active tenant config if no assignments exist
            configs = PaymentProviderConfig.objects.filter(
                tenant=request.user.tenant,
                is_active=True
            )
            for config in configs:
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
                        'checkout_type': provider_cls.get_checkout_type,
                        'is_default': False
                    })
        
        # Sort so default is first
        available_providers.sort(key=lambda x: x['is_default'], reverse=True)

        # Get active product bundles for POS
        from apps.inventory.models import ProductBundle
        bundles = ProductBundle.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        ).prefetch_related('items__product')

        bundles_data = []
        for b in bundles:
            b_items = []
            for item in b.items.all():
                if item.product and item.product.is_active:
                    b_items.append({
                        'product_id': item.product.pk,
                        'product_name': item.product.name,
                        'quantity': float(item.quantity),
                        'unit': item.product.unit_of_measure,
                        'default_price': str(item.product.default_selling_price)
                    })
            if b_items:
                bundles_data.append({
                    'id': b.pk,
                    'name': b.name,
                    'description': b.description or '',
                    'items_count': len(b_items),
                    'items': b_items
                })
        
        available_pos_shops = Location.objects.filter(
            tenant=request.user.tenant,
            location_type='SHOP',
            is_active=True
        ).order_by('name') if is_hq_or_admin else Location.objects.none()

        context = {
            'shop': user_shop,
            'products': json.dumps(products_with_prices),
            'categories': categories,
            'bundles': json.dumps(bundles_data),
            'customers': json.dumps(list(customers), default=str),
            'available_providers': json.dumps(available_providers),
            'shop_settings': shop_settings,
            'shift': open_shift,
            'allow_negative_stock': request.user.tenant.allow_negative_stock,
            'currency_symbol': request.user.tenant.currency_symbol if request.user.tenant.currency else '$',
            'is_hq_or_admin': is_hq_or_admin,
            'available_pos_shops': available_pos_shops,
        }
        
        return render(request, self.template_name, context)



class ShiftOpenView(LoginRequiredMixin, View):
    """Open a new shift."""
    template_name = 'sales/shift_open.html'
    
    def get(self, request):
        user_shop = request.user.location
        
        if not user_shop or user_shop.location_type != 'SHOP':
            messages.error(request, "You must be assigned to a shop.")
            return redirect('core:dashboard')
        
        # Check for existing open shift
        open_shift = Shift.objects.filter(
            tenant=request.user.tenant,
            shop=user_shop,
            attendant=request.user,
            status='OPEN'
        ).first()
        
        if open_shift:
            messages.info(request, "You already have an open shift.")
            return redirect('sales:pos')
        
        return render(request, self.template_name, {'shop': user_shop})
    
    def post(self, request):
        user_shop = request.user.location
        opening_cash = request.POST.get('opening_cash', '0')
        
        try:
            opening_cash = Decimal(opening_cash)
        except:
            opening_cash = Decimal('0')
        
        shift = Shift.objects.create(
            tenant=request.user.tenant,
            shop=user_shop,
            attendant=request.user,
            opening_cash=opening_cash
        )
        
        messages.success(request, f"Shift opened with {opening_cash} opening cash.")
        return redirect('sales:pos')


class SaleWaybillView(LoginRequiredMixin, View):
    """View and print a waybill (delivery note) for a sale."""
    template_name = 'sales/sale_waybill.html'
    
    def get(self, request, pk):
        sale = get_object_or_404(
            Sale.objects.select_related('shop', 'attendant', 'customer', 'dispatched_by'),
            pk=pk,
            tenant=request.user.tenant
        )
        try:
            shop_settings = ShopSettings.objects.get(shop=sale.shop)
        except ShopSettings.DoesNotExist:
            shop_settings = None
        return render(request, self.template_name, {
            'sale': sale,
            'shop_settings': shop_settings,
        })


class ShiftCloseView(LoginRequiredMixin, View):
    """Close current shift."""
    template_name = 'sales/shift_close.html'
    
    def get(self, request, pk):
        shift = get_object_or_404(
            Shift,
            pk=pk,
            tenant=request.user.tenant,
            attendant=request.user,
            status='OPEN'
        )
        
        # Find shop manager for this location
        from apps.core.models import User
        shop_manager = User.objects.filter(
            tenant=request.user.tenant,
            location=shift.shop,
            role__name='SHOP_MANAGER',
            is_active=True
        ).first()
        
        # Calculate sales breakdown using shift's collected sales
        from django.db.models import Sum, Q
        sales_qs = shift._collected_sales_qs()
        
        ecash_sales = sales_qs.filter(payment_method='ECASH').aggregate(
            total=Sum('total'))['total'] or Decimal('0')
        momo_sales = sales_qs.filter(payment_method='MOMO').aggregate(
            total=Sum('total'))['total'] or Decimal('0')
        credit_sales = sales_qs.filter(payment_method='CREDIT').aggregate(
            total=Sum('total'))['total'] or Decimal('0')
        
        all_sales = sales_qs.aggregate(total=Sum('total'))['total'] or Decimal('0')
        payments_on_account = shift.payments_on_account
        total_cash = shift.expected_cash
        
        return render(request, self.template_name, {
            'shift': shift,
            'expected_cash': total_cash,  # Opening + Cash Sales portion + Payments - Refunds
            'total_sales': all_sales,  # All sales
            'cash_sales': shift.total_sales,  # Cash portion only (consistent with expected_cash)
            'ecash_sales': ecash_sales,
            'momo_sales': momo_sales,
            'credit_sales': credit_sales,
            'payments_on_account': payments_on_account,
            'credit_payouts': shift.credit_payouts,
            'refund_payouts': shift.refund_payouts,
            'momo_refund_payouts': shift.momo_refund_payouts,
            'shop_manager': shop_manager,
        })
    
    def post(self, request, pk):
        shift = get_object_or_404(
            Shift,
            pk=pk,
            tenant=request.user.tenant,
            attendant=request.user,
            status='OPEN'
        )
        
        closing_cash = request.POST.get('closing_cash', '0')
        notes = request.POST.get('notes', '')
        
        try:
            closing_cash = Decimal(closing_cash)
        except:
            closing_cash = Decimal('0')
        
        shift.close(closing_cash, notes)
        
        variance = shift.cash_variance
        if variance and variance != 0:
            if variance > 0:
                messages.warning(request, f"Shift closed. Cash overage: {variance}")
            else:
                messages.warning(request, f"Shift closed. Cash shortage: {abs(variance)}")
        else:
            messages.success(request, "Shift closed successfully. Cash balanced.")
        
        # Create cash transfer to shop manager if closing cash > 0
        if closing_cash > 0:
            from apps.core.models import User
            from apps.accounting.models import CashTransfer
            from apps.notifications.models import Notification
            
            user_role = request.user.role.name if request.user.role else None
            
            # Check if the user closing shift IS the shop manager
            if user_role == 'SHOP_MANAGER':
                # Shop manager's own shift — no transfer is needed.
                # The closing cash stays in their hand and is reflected in cash-on-hand
                # by summing their own closed-shift totals in the context processor.
                # Creating a self-transfer here causes double-counting (added as received,
                # then immediately subtracted as sent) so we skip it entirely.
                messages.info(request, f"Shift closed. Cash on hand updated with {request.user.tenant.currency_symbol}{closing_cash}.")
            else:
                # Attendant shift — create pending transfer to shop manager
                shop_manager = User.objects.filter(
                    tenant=request.user.tenant,
                    location=shift.shop,
                    role__name='SHOP_MANAGER',
                    is_active=True
                ).first()
                
                if shop_manager:
                    # Create pending transfer
                    transfer = CashTransfer.objects.create(
                        tenant=request.user.tenant,
                        amount=closing_cash,
                        transfer_type='DEPOSIT',
                        from_user=request.user,
                        from_location=shift.shop,
                        to_user=shop_manager,
                        to_location=shift.shop,
                        notes=f"Shift closing deposit - Shift #{shift.pk}"
                    )
                    
                    # Notify shop manager
                    Notification.objects.create(
                        tenant=request.user.tenant,
                        user=shop_manager,
                        title="Cash Deposit from Attendant",
                        message=f"{request.user.get_full_name() or request.user.email} has deposited {request.user.tenant.currency_symbol}{closing_cash} from their shift. Please confirm receipt.",
                        notification_type='SYSTEM',
                        reference_type='CashTransfer',
                        reference_id=transfer.pk
                    )
                    
                    messages.info(request, f"Cash transfer of {closing_cash} sent to {shop_manager.get_full_name()} for confirmation.")
                else:
                    messages.warning(request, "No shop manager found. Cash is on your hand — please transfer it manually.")
        
        return redirect('core:dashboard')


class ShiftListView(LoginRequiredMixin, SortableMixin, ListView):
    """List of all shifts."""
    model = Shift
    template_name = 'sales/shift_list.html'
    context_object_name = 'shifts'
    paginate_by = 20
    sortable_fields = ['start_time', 'end_time', 'opening_cash', 'closing_cash', 'status']
    default_sort = '-start_time'

    def get_queryset(self):
        qs = Shift.objects.filter(tenant=self.request.user.tenant)
        
        # Filters
        shop = self.request.GET.get('shop')
        attendant = self.request.GET.get('attendant')
        status = self.request.GET.get('status')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        
        if shop:
            qs = qs.filter(shop_id=shop)
        if attendant:
            qs = qs.filter(attendant_id=attendant)
        if status:
            qs = qs.filter(status=status)
        if date_from:
            qs = qs.filter(start_time__date__gte=date_from)
        if date_to:
            qs = qs.filter(start_time__date__lte=date_to)
            
        qs = self.apply_sorting(qs).select_related('shop', 'attendant')
        
        role_name = self.request.user.role.name if self.request.user.role else ''
        
        if role_name in ['ATTENDANT', 'SHOP_ATTENDANT']:
            qs = qs.filter(attendant=self.request.user)
        elif role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            qs = qs.filter(shop=self.request.user.location)
            
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['role_name'] = self.request.user.role.name if self.request.user.role else ''
        
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        context['shops'] = Location.objects.filter(tenant=self.request.user.tenant, location_type='SHOP')
        context['attendants'] = User.objects.filter(tenant=self.request.user.tenant, role__name='ATTENDANT')
        
        context['current_shop'] = self.request.GET.get('shop', '')
        context['current_attendant'] = self.request.GET.get('attendant', '')
        context['current_status'] = self.request.GET.get('status', '')
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        
        # Shift summaries
        from django.db.models import Sum, Count, Q
        from decimal import Decimal
        qs = self.get_queryset()
        shift_agg = qs.aggregate(
            total_shifts=Count('id'),
            open_shifts=Count('id', filter=Q(status='OPEN')),
            closed_shifts=Count('id', filter=Q(status='CLOSED')),
            total_closing_cash=Sum('closing_cash', filter=Q(status='CLOSED')),
            total_opening_cash=Sum('opening_cash'),
        )
        context['summary_total_shifts'] = shift_agg['total_shifts'] or 0
        context['summary_open_shifts'] = shift_agg['open_shifts'] or 0
        context['summary_closed_shifts'] = shift_agg['closed_shifts'] or 0
        context['summary_closing_cash'] = shift_agg['total_closing_cash'] or Decimal('0.00')
        context['summary_opening_cash'] = shift_agg['total_opening_cash'] or Decimal('0.00')
        
        return context


class ShiftReceiptView(LoginRequiredMixin, View):
    """Printable receipt for a shift."""
    template_name = 'sales/shift_receipt.html'
    
    def get(self, request, pk):
        shift = get_object_or_404(Shift, pk=pk, tenant=request.user.tenant)
        
        # Check permissions
        role_name = request.user.role.name if request.user.role else ''
        if role_name == 'ATTENDANT' and shift.attendant != request.user:
            messages.error(request, "You don't have permission to view this shift.")
            return redirect('sales:shift_list')
        elif role_name == 'SHOP_MANAGER' and shift.shop != request.user.location:
            messages.error(request, "You don't have permission to view this shift.")
            return redirect('sales:shift_list')
            
        return render(request, self.template_name, {
            'shift': shift,
            'current_tenant': request.user.tenant,
        })


@login_required
def api_shift_detail(request, pk):
    """Return JSON details for a shift."""
    try:
        shift = Shift.objects.get(pk=pk, tenant=request.user.tenant)
        
        # Check permissions
        role_name = request.user.role.name if request.user.role else ''
        if role_name == 'ATTENDANT' and shift.attendant != request.user:
            return JsonResponse({'error': 'Permission denied'}, status=403)
        elif role_name == 'SHOP_MANAGER' and shift.shop != request.user.location:
            return JsonResponse({'error': 'Permission denied'}, status=403)
            
        # Calculate sales breakdown
        from django.db.models import Sum
        from decimal import Decimal
        sales_qs = shift.sales.filter(status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])
        ecash_sales = sales_qs.filter(payment_method='ECASH').aggregate(total=Sum('total'))['total'] or Decimal('0')
        momo_sales = sales_qs.filter(payment_method='MOMO').aggregate(total=Sum('total'))['total'] or Decimal('0')
        credit_sales = sales_qs.filter(payment_method='CREDIT').aggregate(total=Sum('total'))['total'] or Decimal('0')
        mixed_cash = sales_qs.filter(payment_method='MIXED').aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
        mixed_credit_total = sales_qs.filter(payment_method='MIXED').aggregate(total=Sum('total'))['total'] or Decimal('0')
        mixed_credit = mixed_credit_total - mixed_cash
        
        total_credit_sales = credit_sales + mixed_credit
        cash_sales = shift.total_sales
        payments_on_account = shift.payments_on_account
        
        def fmt(val):
            return f"{float(val):.2f}"
            
        data = {
            'id': shift.pk,
            'shop': shift.shop.name,
            'attendant': shift.attendant.get_full_name() or shift.attendant.email,
            'status': shift.get_status_display(),
            'start_time': shift.start_time.strftime('%b %d, %Y %H:%M'),
            'end_time': shift.end_time.strftime('%b %d, %Y %H:%M') if shift.end_time else 'Active',
            'opening_cash': fmt(shift.opening_cash),
            'closing_cash': fmt(shift.closing_cash) if shift.closing_cash is not None else 'N/A',
            'cash_sales': fmt(cash_sales),
            'ecash_sales': fmt(ecash_sales),
            'momo_sales': fmt(momo_sales),
            'credit_sales': fmt(total_credit_sales),
            'payments_on_account': fmt(payments_on_account),
            'expected_cash': fmt(shift.expected_cash),
            'variance': fmt(shift.cash_variance) if shift.cash_variance is not None else 'N/A',
            'notes': shift.notes
        }
        return JsonResponse(data)
    except Shift.DoesNotExist:
        return JsonResponse({'error': 'Shift not found'}, status=404)


class SaleListView(LoginRequiredMixin, SortableMixin, ListView):
    """List sales for the shop."""
    model = Sale
    template_name = 'sales/sale_list.html'
    context_object_name = 'sales'
    sortable_fields = [
        'created_at', 'latest_activity', 'sale_number', 'total', 'amount_paid',
        'status', 'payment_method', 'attendant', 'attendant__username', 'attendant__name', 'attendant__first_name'
    ]
    sort_mappings = {
        'attendant': ['attendant__first_name', 'attendant__last_name', 'attendant__email'],
        'attendant__username': ['attendant__first_name', 'attendant__last_name', 'attendant__email'],
        'attendant__name': ['attendant__first_name', 'attendant__last_name', 'attendant__email'],
        'attendant__first_name': ['attendant__first_name', 'attendant__last_name', 'attendant__email'],
    }
    default_sort = '-created_at'
    
    def get_paginate_by(self, queryset):
        try:
            return int(self.request.GET.get('per_page', 25))
        except (ValueError, TypeError):
            return 25
            
    def get_queryset(self):
        from datetime import datetime
        
        user = self.request.user
        role_name = user.role.name if user.role else None
        
        from django.db.models import Exists, OuterRef, F, Q
        from django.db.models.functions import Greatest, Coalesce
        from .models import RefundRequest

        queryset = Sale.objects.filter(
            tenant=user.tenant
        ).select_related(
            'shop', 'shop__shop_settings', 'attendant', 'customer'
        ).prefetch_related(
            'items', 'refund_requests'
        ).annotate(
            _has_pending_refund=Exists(
                RefundRequest.objects.filter(sale=OuterRef('pk'), status='PENDING')
            ),
            _has_pending_halt=Exists(
                RefundRequest.objects.filter(sale=OuterRef('pk'), status='PENDING', is_partial_halt=True)
            ),
            _has_approved_pending_payout=Exists(
                RefundRequest.objects.filter(sale=OuterRef('pk'), status='APPROVED', payout_status='PENDING')
            ),
            latest_activity=Greatest(
                'created_at',
                Coalesce('updated_at', 'created_at'),
                Coalesce('dispatched_at', 'created_at')
            )
        )
        
        # For shop-based users (Shop Manager, Attendant), filter by their shop
        # Auditors, Accountants, and Admins see all shops by default
        if role_name not in ['AUDITOR', 'ACCOUNTANT', 'ADMIN']:
            if user.location and user.location.location_type == 'SHOP':
                queryset = queryset.filter(shop=user.location)
                
            # Attendants only see their own sales/invoices
            if role_name == 'SHOP_ATTENDANT':
                queryset = queryset.filter(attendant=user)
        else:
            # Shop filter for Auditor/Accountant/Admin
            shop_id = self.request.GET.get('shop')
            if shop_id:
                queryset = queryset.filter(shop_id=shop_id)
            
            # Attendant filter
            attendant_id = self.request.GET.get('attendant')
            if attendant_id:
                queryset = queryset.filter(attendant_id=attendant_id)
        
        # Date range filter (for all roles)
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
            # Payment method filter
        payment = self.request.GET.get('payment')
        if payment:
            queryset = queryset.filter(payment_method=payment)
            
        # Refund status filter
        refund_status = self.request.GET.get('refund_status')
        if refund_status == 'pending':
            from django.db.models import Q
            queryset = queryset.filter(Q(_has_pending_refund=True) | Q(_has_approved_pending_payout=True))
        elif refund_status == 'pending_approval':
            queryset = queryset.filter(_has_pending_refund=True)
        elif refund_status == 'pending_payout':
            queryset = queryset.filter(_has_approved_pending_payout=True)
        elif refund_status == 'pending_halt':
            queryset = queryset.filter(_has_pending_halt=True)
        elif refund_status == 'halted':
            queryset = queryset.filter(is_halted=True)
        elif refund_status == 'refunded':
            queryset = queryset.filter(status='REFUNDED')
        elif refund_status == 'all_refunds':
            from django.db.models import Q
            queryset = queryset.filter(
                Q(_has_pending_refund=True) |
                Q(_has_approved_pending_payout=True) |
                Q(is_halted=True) |
                Q(status='REFUNDED')
            )
        elif refund_status == 'no_refunds':
            from django.db.models import Q
            queryset = queryset.filter(
                ~Q(status='REFUNDED'),
                _has_pending_refund=False,
                _has_approved_pending_payout=False,
                is_halted=False
            )

        # Dispatch status filter
        dispatch_status = self.request.GET.get('dispatch_status')
        if dispatch_status == 'pending':
            from django.db.models import Q
            queryset = queryset.filter(
                Q(status__in=['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']) | Q(status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'], is_dispatched=False)
            )
        elif dispatch_status in ['dispatched', 'completed']:
            queryset = queryset.filter(status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'], is_dispatched=True)
            
        # Sync conflict filter
        sync_conflict = self.request.GET.get('sync_conflict')
        if sync_conflict in ['1', 'true', 'yes']:
            queryset = queryset.filter(has_sync_conflict=True)

        # Text search (Invoice Number, Customer Name, Customer Phone)
        q = self.request.GET.get('q', '').strip()
        if q:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(sale_number__icontains=q) |
                Q(customer__name__icontains=q) |
                Q(customer__phone__icontains=q) |
                Q(customer_name__icontains=q) |
                Q(customer_phone__icontains=q)
            )
         
        return self.apply_sorting(queryset)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role_name = user.role.name if user.role else None
        
        # Check if full view (Auditor/Accountant/Admin)
        context['is_full_view'] = role_name in ['AUDITOR', 'ACCOUNTANT', 'ADMIN']

        # Total sync conflicts count for user location
        conflict_scope = {'tenant': user.tenant, 'has_sync_conflict': True}
        if role_name not in ['AUDITOR', 'ACCOUNTANT', 'ADMIN'] and user.location:
            conflict_scope['shop'] = user.location
        context['total_sync_conflicts_count'] = Sale.objects.filter(**conflict_scope).count()
        
        # Summary metrics on filtered queryset
        from django.db.models import Sum, Count, Q
        filtered_qs = self.get_queryset()
        summary = filtered_qs.order_by().aggregate(
            total_sales_value=Sum('total', filter=~Q(status__in=['VOIDED', 'REFUNDED'])),
            total_paid_value=Sum('amount_paid', filter=~Q(status__in=['VOIDED', 'REFUNDED'])),
            total_count=Count('id'),
            completed_count=Count('id', filter=Q(status='COMPLETED')),
            pending_dispatch_count=Count('id', filter=Q(status='PENDING_DISPATCH')),
            partially_dispatched_count=Count('id', filter=Q(status='PARTIALLY_DISPATCHED')),
            voided_count=Count('id', filter=Q(status='VOIDED')),
            voided_value=Sum('total', filter=Q(status='VOIDED')),
            cash_sales_full=Sum('total', filter=Q(payment_method='CASH', status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])),
            cash_sales_mixed=Sum('amount_paid', filter=Q(payment_method='MIXED', status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])),
            other_cash_sales=Sum('amount_paid', filter=~Q(payment_method__in=['CASH', 'ECASH', 'MOMO', 'CREDIT', 'PENDING_INVOICE', 'MIXED']) & Q(status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])),
            ecash_sales_value=Sum('total', filter=Q(payment_method='ECASH', status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])),
            momo_sales_value=Sum('total', filter=Q(payment_method='MOMO', status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])),
            credit_sales_full=Sum('total', filter=Q(payment_method__in=['CREDIT', 'PENDING_INVOICE'], status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])),
            mixed_sales_total=Sum('total', filter=Q(payment_method='MIXED', status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])),
            dispatched_count=Count('id', filter=Q(is_dispatched=True, status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'])),
        )

        cash_full = summary['cash_sales_full'] or Decimal('0.00')
        cash_mixed = summary['cash_sales_mixed'] or Decimal('0.00')
        other_cash = summary['other_cash_sales'] or Decimal('0.00')

        mixed_total = summary['mixed_sales_total'] or Decimal('0.00')
        mixed_credit = max(mixed_total - cash_mixed, Decimal('0.00'))
        credit_full = summary['credit_sales_full'] or Decimal('0.00')

        context['summary_total_sales_value'] = summary['total_sales_value'] or Decimal('0.00')
        context['summary_total_paid_value'] = summary['total_paid_value'] or Decimal('0.00')
        context['summary_total_count'] = summary['total_count'] or 0
        context['summary_completed_count'] = summary['completed_count'] or 0
        context['summary_pending_dispatch_count'] = summary['pending_dispatch_count'] or 0
        context['summary_partially_dispatched_count'] = summary['partially_dispatched_count'] or 0
        context['summary_dispatched_count'] = summary['dispatched_count'] or 0
        context['summary_voided_count'] = summary['voided_count'] or 0
        context['summary_voided_value'] = summary['voided_value'] or Decimal('0.00')
        context['summary_cash_sales_value'] = cash_full + cash_mixed + other_cash
        context['summary_ecash_sales_value'] = summary['ecash_sales_value'] or Decimal('0.00')
        context['summary_momo_sales_value'] = summary['momo_sales_value'] or Decimal('0.00')
        context['summary_credit_sales_value'] = credit_full + mixed_credit

        if context['is_full_view']:
            # Shops for filter
            context['shops'] = Location.objects.filter(
                tenant=user.tenant,
                location_type='SHOP',
                is_active=True
            )
            # Attendants for filter
            from apps.core.models import User as TenantUser
            context['attendants'] = TenantUser.objects.filter(
                tenant=user.tenant,
                role__name__in=['SHOP_ATTENDANT', 'SHOP_MANAGER'],
                is_active=True
            ).order_by('first_name', 'email')
        
        # Quick filter badge counts for refund/activity navigation pills
        from .models import RefundRequest
        base_counts_qs = Sale.objects.filter(tenant=user.tenant)
        rf_filter = Q(tenant=user.tenant)
        if role_name not in ['AUDITOR', 'ACCOUNTANT', 'ADMIN']:
            if user.location and user.location.location_type == 'SHOP':
                base_counts_qs = base_counts_qs.filter(shop=user.location)
                rf_filter &= Q(sale__shop=user.location)
            if role_name == 'SHOP_ATTENDANT':
                base_counts_qs = base_counts_qs.filter(attendant=user)
                rf_filter &= Q(sale__attendant=user)
        else:
            if self.request.GET.get('shop'):
                base_counts_qs = base_counts_qs.filter(shop_id=self.request.GET.get('shop'))
                rf_filter &= Q(sale__shop_id=self.request.GET.get('shop'))
            if self.request.GET.get('attendant'):
                base_counts_qs = base_counts_qs.filter(attendant_id=self.request.GET.get('attendant'))
                rf_filter &= Q(sale__attendant_id=self.request.GET.get('attendant'))

        sale_counts = base_counts_qs.order_by().aggregate(
            cnt_halted=Count('id', filter=Q(is_halted=True)),
            cnt_refunded=Count('id', filter=Q(status='REFUNDED')),
            cnt_total=Count('id'),
        )
        rf_counts = RefundRequest.objects.filter(rf_filter).order_by().aggregate(
            cnt_pending_approval=Count('sale_id', distinct=True, filter=Q(status='PENDING')),
            cnt_pending_payout=Count('sale_id', distinct=True, filter=Q(status='APPROVED', payout_status='PENDING')),
        )
        context['count_pending_approval'] = rf_counts['cnt_pending_approval'] or 0
        context['count_pending_payout'] = rf_counts['cnt_pending_payout'] or 0
        context['count_halted'] = sale_counts['cnt_halted'] or 0
        context['count_refunded'] = sale_counts['cnt_refunded'] or 0
        context['count_all_refunds'] = (
            context['count_pending_approval'] +
            context['count_pending_payout'] +
            context['count_halted'] +
            context['count_refunded']
        )
        context['count_no_refunds'] = max(0, (sale_counts['cnt_total'] or 0) - context['count_all_refunds'])

        # Preserve filter values
        context['per_page'] = self.get_paginate_by(None)
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['selected_shop'] = self.request.GET.get('shop', '')
        context['selected_attendant'] = self.request.GET.get('attendant', '')
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_payment'] = self.request.GET.get('payment', '')
        context['selected_dispatch_status'] = self.request.GET.get('dispatch_status', '')
        context['selected_refund_status'] = self.request.GET.get('refund_status', '')
        context['q'] = self.request.GET.get('q', '')
        
        # Build sale_provider_map for the sales on this page
        sales = context.get('sales', [])
        if sales:
            from apps.payments.models import ECashLedger
            ecash_sale_ids = [sale.id for sale in sales if sale.payment_method == 'ECASH']
            if ecash_sale_ids:
                ledger_entries = ECashLedger.objects.filter(
                    tenant=user.tenant,
                    reference_type='Sale',
                    reference_id__in=ecash_sale_ids
                ).select_related('provider_config')
                
                context['sale_provider_map'] = {
                    entry.reference_id: entry.provider_config.nickname if entry.provider_config else 'E-Cash'
                    for entry in ledger_entries
                }
            else:
                context['sale_provider_map'] = {}

        # Inject available ECASH providers for the shop (or all for HQ)
        from apps.payments.models import ShopPaymentAssignment, PaymentProviderConfig
        user_shop = self.request.user.location
        
        configs_to_process = []
        if user_shop and user_shop.location_type == 'SHOP':
            assignments = ShopPaymentAssignment.objects.filter(
                shop=user_shop,
                provider_config__is_active=True
            ).select_related('provider_config').order_by('priority')
            configs_to_process = [a.provider_config for a in assignments]
        else:
            configs_to_process = PaymentProviderConfig.objects.filter(
                tenant=user.tenant,
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
                
        context['available_providers'] = unique_providers

        # Shop settings for receipt/payment configuration
        if user_shop and user_shop.location_type == 'SHOP':
            from apps.sales.models import ShopSettings
            shop_settings, _ = ShopSettings.objects.get_or_create(
                tenant=user.tenant,
                shop=user_shop,
                defaults={'receipt_printer_type': 'THERMAL_80MM', 'disallow_manual_momo_amount': True}
            )
            context['shop_settings'] = shop_settings
        
        return context


class DispatchListView(SaleListView):
    """List of all fully or partially dispatched sales."""
    template_name = 'sales/dispatch_list.html'

    def get_queryset(self):
        from django.db.models import Q, Exists, OuterRef
        from .models import SaleItem
        queryset = super().get_queryset()
        has_dispatched_items = Exists(
            SaleItem.objects.filter(sale=OuterRef('pk'), dispatched_quantity__gt=0)
        )
        return queryset.filter(
            Q(is_dispatched=True) | Q(status='PARTIALLY_DISPATCHED') | has_dispatched_items
        )

    def get_context_data(self, **kwargs):
        # DispatchListView only displays summary cards for dispatched goods; avoid heavy refund badge aggregations
        context = super(SaleListView, self).get_context_data(**kwargs)
        user = self.request.user
        role_name = user.role.name if user.role else None
        context['is_full_view'] = role_name in ['AUDITOR', 'ACCOUNTANT', 'ADMIN']
        
        from django.db.models import Sum, Count, Q
        filtered_qs = self.get_queryset()
        summary = filtered_qs.order_by().select_related(None).prefetch_related(None).aggregate(
            total_sales_value=Sum('total', filter=~Q(status__in=['VOIDED', 'REFUNDED'])),
            total_paid_value=Sum('amount_paid', filter=~Q(status__in=['VOIDED', 'REFUNDED'])),
            total_count=Count('id'),
            dispatched_count=Count('id', filter=Q(is_dispatched=True)),
        )
        context['summary_total_sales_value'] = summary['total_sales_value'] or Decimal('0.00')
        context['summary_total_paid_value'] = summary['total_paid_value'] or Decimal('0.00')
        context['summary_total_count'] = summary['total_count'] or 0
        context['summary_dispatched_count'] = summary['dispatched_count'] or 0
        
        context['per_page'] = self.get_paginate_by(None)
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['q'] = self.request.GET.get('q', '')
        return context


class SaleDetailView(LoginRequiredMixin, DetailView):
    """View sale details / receipt."""
    model = Sale
    template_name = 'sales/sale_receipt.html'
    context_object_name = 'sale'
    
    def get_queryset(self):
        return Sale.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('shop', 'attendant').prefetch_related('items__product')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get shop settings for receipt format
        try:
            context['shop_settings'] = ShopSettings.objects.get(
                tenant=self.request.user.tenant,
                shop=self.object.shop
            )
        except ShopSettings.DoesNotExist:
            context['shop_settings'] = None
        
        return context


@login_required
def api_sale_detail(request, pk_or_number):
    """Return sale detail as JSON for modal display."""
    queryset = Sale.objects.select_related('shop', 'attendant', 'customer').prefetch_related('items__product')
    
    val_str = str(pk_or_number).strip()
    if val_str.startswith('REF') or val_str.startswith('refund-'):
        from apps.sales.models import RefundRequest
        if val_str.startswith('refund-'):
            ref_pk = val_str.split('-', 1)[1]
            rf = get_object_or_404(RefundRequest, pk=ref_pk, tenant=request.user.tenant)
        else:
            rf = get_object_or_404(RefundRequest, refund_number=val_str, tenant=request.user.tenant)
        sale = rf.sale
    elif val_str.startswith('S'):
        sale = get_object_or_404(queryset, sale_number=val_str, tenant=request.user.tenant)
    else:
        sale = get_object_or_404(queryset, pk=val_str, tenant=request.user.tenant)
        
    active_rf = (
        sale.refund_requests.filter(status='PENDING').first() or
        sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first() or
        sale.refund_requests.filter(status='APPROVED', payout_status='PAID').first() or
        sale.refund_requests.first()
    )

    is_partial_return = bool(active_rf and active_rf.is_partial_return)
    is_partial_halt = bool((active_rf and active_rf.is_partial_halt) or (not active_rf and sale.is_halted))
    is_full_refund = bool(
        sale.status == 'REFUNDED' or
        (active_rf and not is_partial_halt and not is_partial_return)
    )
    is_refunding = bool(sale.is_halted or sale.status == 'REFUNDED' or (active_rf and active_rf.status in ['PENDING', 'APPROVED']))

    returned_items_map = {}
    if is_partial_return and active_rf and active_rf.returned_items:
        for entry in active_rf.returned_items:
            try:
                it_id = int(entry.get('sale_item_id') or entry.get('id'))
                returned_items_map[it_id] = entry
            except (ValueError, TypeError):
                pass

    items = []
    for item in sale.items.all():
        qty = item.quantity or Decimal('0.00')
        disp_qty = item.dispatched_quantity or Decimal('0.00')
        rem_qty = item.remaining_quantity or Decimal('0.00')
        tot = item.total or Decimal('0.00')
        if qty > Decimal('0.00'):
            disp_val = (disp_qty * (tot / qty)).quantize(Decimal('0.01'))
            rem_val = tot - disp_val
        else:
            disp_val = Decimal('0.00')
            rem_val = Decimal('0.00')

        if is_partial_return:
            ret_entry = returned_items_map.get(item.id)
            if ret_entry:
                ret_qty = Decimal(str(ret_entry.get('return_qty', '0'))).quantize(Decimal('0.01'))
                ret_val = Decimal(str(ret_entry.get('return_val', ret_qty * item.unit_price))).quantize(Decimal('0.01'))
            else:
                ret_qty = Decimal('0.00')
                ret_val = Decimal('0.00')
            if active_rf and active_rf.payout_status == 'PAID':
                kept_qty = disp_qty.quantize(Decimal('0.01'))
                kept_val = disp_val.quantize(Decimal('0.01'))
                disp_qty = (kept_qty + ret_qty).quantize(Decimal('0.01'))
                disp_val = (kept_val + ret_val).quantize(Decimal('0.01'))
            else:
                kept_qty = max(Decimal('0.00'), disp_qty - ret_qty).quantize(Decimal('0.01'))
                kept_val = max(Decimal('0.00'), disp_val - ret_val).quantize(Decimal('0.01'))
        elif is_full_refund:
            ret_qty = disp_qty if disp_qty > Decimal('0.00') else qty
            ret_val = tot
            kept_qty = Decimal('0.00')
            kept_val = Decimal('0.00')
        else:
            ret_qty = Decimal('0.00')
            ret_val = Decimal('0.00')
            kept_qty = disp_qty
            kept_val = disp_val

        items.append({
            'id': item.id,
            'product_name': item.product.name,
            'sku': item.product.sku,
            'quantity': str(qty),
            'dispatched_quantity': str(disp_qty),
            'remaining_quantity': str(rem_qty),
            'returned_quantity': str(ret_qty),
            'returned_value': str(ret_val),
            'retained_quantity': str(kept_qty),
            'retained_value': str(kept_val),
            'is_fully_dispatched': item.is_fully_dispatched,
            'unit_price': str(item.unit_price),
            'total': str(tot),
            'dispatched_value': str(disp_val),
            'remaining_value': str(rem_val),
        })

    # Look up actual overpayment credited to customer account from CustomerTransaction.
    # The sale.change_given field is not always populated; the source of truth is the
    # CustomerTransaction record with description starting 'Overpayment'.
    overpayment_credited = None
    if sale.customer:
        from apps.customers.models import CustomerTransaction
        op_tx = CustomerTransaction.objects.filter(
            customer=sale.customer,
            reference_id=sale.sale_number,
            description__startswith='Overpayment',
        ).first()
        if op_tx:
            overpayment_credited = str(op_tx.amount)

    # ── Dispatch History: group InventoryLedger SALE entries by (created_by, approx timestamp) ──
    from apps.inventory.models import InventoryLedger
    ledger_entries = InventoryLedger.objects.filter(
        tenant=sale.tenant,
        transaction_type='SALE',
        reference_type='Sale',
        reference_id=sale.pk,
    ).select_related('product', 'created_by').order_by('created_at')

    # Group by minute+user to cluster simultaneous dispatches
    dispatch_events = {}
    for entry in ledger_entries:
        ts = entry.created_at
        bucket = ts.strftime('%Y-%m-%d %H:%M') + str(entry.created_by_id or '')
        if bucket not in dispatch_events:
            dispatch_events[bucket] = {
                'dispatched_at': ts.strftime('%b %d, %Y %H:%M'),
                'dispatched_by': entry.created_by.get_full_name() if entry.created_by else 'System',
                'items': []
            }
        dispatch_events[bucket]['items'].append({
            'product_name': entry.product.name,
            'qty': str(abs(entry.quantity)),
        })

    dispatch_history = list(dispatch_events.values())

    ecash_metadata = None
    if sale.payment_method == 'ECASH':
        from apps.payments.models import ECashLedger
        ecash_tx = ECashLedger.objects.filter(
            tenant=sale.tenant,
            reference_type='Sale',
            reference_id=sale.pk,
            transaction_type='PAYMENT'
        ).first()
        if ecash_tx and ecash_tx.metadata:
            ecash_metadata = ecash_tx.metadata

    # Calculate refund figures for modal displays
    calc_disp_total = sale.dispatched_total
    calc_undisp_total = sale.undispatched_total
    calc_refund_amt = sale.calculated_refund_amount
    calc_is_partial = bool(sale.is_partially_dispatched or (sale.is_halted and calc_disp_total > Decimal('0.00')) or (sale.amount_paid and sale.total and sale.amount_paid > sale.total))
    if sale.amount_paid and sale.amount_paid > calc_disp_total:
        calc_debt_red = max(Decimal('0.00'), sale.total - sale.amount_paid)
    else:
        calc_debt_red = calc_undisp_total

    if active_rf:
        effective_paid = active_rf.effective_amount_paid
        effective_disp = active_rf.effective_dispatched_total
        effective_orig_total = active_rf.original_sale_total
        active_refund_val = active_rf.refund_amount
        active_debt_red = active_rf.debt_reduction_amount
    else:
        effective_paid = sale.amount_paid or Decimal('0.00')
        effective_disp = calc_disp_total if not is_full_refund else Decimal('0.00')
        effective_orig_total = sale.total or Decimal('0.00')
        active_refund_val = calc_refund_amt
        active_debt_red = calc_debt_red

    if (sale.is_halted or (active_rf and active_rf.is_partial_halt)) and (effective_disp + active_refund_val) > effective_paid:
        effective_paid = effective_disp + active_refund_val

    if is_full_refund:
        net_retained = Decimal('0.00')
        effective_disp = Decimal('0.00')
    elif is_refunding:
        net_retained = effective_disp
    else:
        net_retained = sale.total or Decimal('0.00')

    items_total_sum = sum((item.total or Decimal('0.00')) for item in sale.items.all())
    legacy_settled_amount = Decimal('0.00')
    if is_refunding and effective_orig_total > items_total_sum:
        legacy_settled_amount = effective_orig_total - items_total_sum

    data = {
        'id': sale.pk,
        'sale_id': sale.pk,
        'sale_number': sale.sale_number,
        'created_at': sale.created_at.strftime('%b %d, %Y %H:%M'),
        'shop': sale.shop.name,
        'attendant': sale.attendant.get_full_name() or sale.attendant.email,
        'payment_method': sale.get_payment_method_display(),
        'payment_method_code': sale.payment_method,
        'status': sale.get_status_display(),
        'status_code': sale.status,
        'subtotal': str(sale.subtotal),
        'discount_amount': str(sale.discount_amount) if sale.discount_amount else None,
        'tax_amount': str(sale.tax_amount) if sale.tax_amount else None,
        'total': str(sale.total),
        'amount_paid': str(sale.amount_paid),
        'effective_amount_paid': str(effective_paid),
        'original_sale_total': str(effective_orig_total),
        'effective_dispatched_total': str(effective_disp),
        'active_refund_amount': str(active_refund_val),
        'active_debt_reduction': str(active_debt_red),
        'net_retained_amount': str(net_retained),
        'is_refunding': is_refunding,
        'legacy_settled_amount': str(legacy_settled_amount) if legacy_settled_amount > Decimal('0.00') else None,
        'change_given': str(sale.change_given) if sale.change_given else None,
        'overpayment_credited': overpayment_credited,
        'customer': sale.customer.name if sale.customer else (sale.customer_name or None),
        'customer_id': sale.customer.pk if sale.customer else None,
        'has_registered_customer': bool(sale.customer),
        'customer_name': sale.customer_name,
        'customer_phone': sale.customer_phone,
        'is_dispatched': sale.is_dispatched,
        'all_items_dispatched': sale.all_items_dispatched,
        'is_partially_dispatched': sale.is_partially_dispatched,
        'is_full_refund': is_full_refund,
        'is_partial_return': is_partial_return,
        'is_partial_halt': is_partial_halt,
        'returned_items': active_rf.returned_items if (active_rf and active_rf.is_partial_return) else None,
        'allow_partial_dispatch': sale.shop.allow_partial_dispatch if sale.shop else True,
        'is_halted': sale.is_halted,
        'can_be_resumed': sale.can_be_resumed,
        'calculated_refund_amount': str(calc_refund_amt),
        'calculated_is_partial_halt': calc_is_partial,
        'calculated_debt_reduction': str(calc_debt_red),
        'dispatched_total': str(calc_disp_total),
        'undispatched_total': str(calc_undisp_total),
        'items': items,
        'dispatch_history': dispatch_history,
        'ecash_metadata': ecash_metadata,
        'refunds': [
            {
                'id': rf_obj.id,
                'refund_number': rf_obj.refund_number,
                'status': rf_obj.get_status_display(),
                'status_code': rf_obj.status,
                'payout_status': rf_obj.get_payout_status_display(),
                'refund_amount': str(rf_obj.refund_amount),
                'is_partial_halt': rf_obj.is_partial_halt,
                'is_partial_return': rf_obj.is_partial_return,
                'returned_items': rf_obj.returned_items,
                'debt_reduction_amount': str(rf_obj.debt_reduction_amount),
                'dispatched_total': str(rf_obj.effective_dispatched_total),
                'undispatched_total': str(rf_obj.undispatched_total),
                'original_sale_total': str(rf_obj.original_sale_total),
                'reason': rf_obj.reason,
                'refund_method': rf_obj.get_refund_method_display(),
                'created_at': rf_obj.created_at.strftime('%b %d, %Y %H:%M'),
                'paid_at': rf_obj.paid_at.strftime('%b %d, %Y %H:%M') if rf_obj.paid_at else None,
            }
            for rf_obj in sale.refund_requests.all()
        ],
        'has_pending_refund': sale.refund_requests.filter(status='PENDING').exists(),
        'pending_refund_info': {
            'id': sale.refund_requests.filter(status='PENDING').first().id,
            'refund_number': sale.refund_requests.filter(status='PENDING').first().refund_number,
            'is_partial_halt': sale.refund_requests.filter(status='PENDING').first().is_partial_halt,
            'is_partial_return': sale.refund_requests.filter(status='PENDING').first().is_partial_return,
            'returned_items': sale.refund_requests.filter(status='PENDING').first().returned_items,
            'refund_amount': str(sale.refund_requests.filter(status='PENDING').first().refund_amount),
            'dispatched_total': str(sale.refund_requests.filter(status='PENDING').first().effective_dispatched_total),
            'undispatched_total': str(sale.refund_requests.filter(status='PENDING').first().undispatched_total),
            'original_sale_total': str(sale.refund_requests.filter(status='PENDING').first().original_sale_total),
            'debt_reduction_amount': str(sale.refund_requests.filter(status='PENDING').first().debt_reduction_amount),
            'effective_amount_paid': str(sale.refund_requests.filter(status='PENDING').first().effective_amount_paid),
            'reason': sale.refund_requests.filter(status='PENDING').first().reason,
            'created_at': sale.refund_requests.filter(status='PENDING').first().created_at.strftime('%b %d, %Y %H:%M'),
        } if sale.refund_requests.filter(status='PENDING').exists() else None,
        'has_approved_pending_payout': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').exists(),
        'approved_refund_info': {
            'id': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().id,
            'refund_number': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().refund_number,
            'is_partial_halt': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().is_partial_halt,
            'is_partial_return': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().is_partial_return,
            'returned_items': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().returned_items,
            'refund_amount': str(sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().refund_amount),
            'dispatched_total': str(sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().effective_dispatched_total),
            'undispatched_total': str(sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().undispatched_total),
            'original_sale_total': str(sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().original_sale_total),
            'refund_method': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().get_refund_method_display(),
            'debt_reduction_amount': str(sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().debt_reduction_amount),
            'effective_amount_paid': str(sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().effective_amount_paid),
            'reason': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().reason,
            'created_at': sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').first().created_at.strftime('%b %d, %Y %H:%M'),
        } if sale.refund_requests.filter(status='APPROVED', payout_status='PENDING').exists() else None,
    }
    
    disallow_manual_momo = True
    shop_settings = getattr(sale.shop, 'shop_settings', None)
    if not shop_settings and sale.shop:
        try:
            from apps.sales.models import ShopSettings
            shop_settings = ShopSettings.objects.get(shop=sale.shop)
        except ShopSettings.DoesNotExist:
            shop_settings = None
    if shop_settings:
        disallow_manual_momo = shop_settings.disallow_manual_momo_amount
    data['disallow_manual_momo_amount'] = disallow_manual_momo

    return JsonResponse(data)


@login_required
@require_POST
def api_refund_sale(request, pk):
    """
    Refund a completed sale.

    - Shop Manager: creates a RefundRequest (PENDING) and notifies accountants/admin.
    - Accountant / Admin: executes the refund immediately (no approval needed).
    """
    import json

    tenant = request.user.tenant

    # Check if refunds are enabled for the tenant
    if not tenant.enable_refunds:
        return JsonResponse({'success': False, 'error': 'Refunds are disabled for this tenant.'}, status=403)

    role_name = request.user.role.name if request.user.role else None
    if tenant.use_strict_sales_workflow:
        if role_name != 'SHOP_CASHIER':
            return JsonResponse({'success': False, 'error': 'In strict sales mode, only cashiers can request refunds.'}, status=403)
    elif role_name not in ['SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN', 'SHOP_CASHIER']:
        return JsonResponse({'success': False, 'error': 'You do not have permission to request or process refunds.'}, status=403)

    sale = get_object_or_404(Sale, pk=pk, tenant=tenant)

    # Shop managers and cashiers can only refund their own shop
    if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and sale.shop != request.user.location:
        return JsonResponse({'success': False, 'error': 'You can only request refunds for your own shop.'}, status=403)

    if sale.status == 'REFUNDED':
        return JsonResponse({'success': False, 'error': 'Sale is already refunded.'}, status=400)

    if sale.status in ['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']:
        if not sale.is_halted:
            return JsonResponse({
                'success': False,
                'error': "This sale has undispatched items. The Shop Manager must halt dispatch before a refund can be requested."
            }, status=400)
    elif sale.status != 'COMPLETED':
        return JsonResponse({'success': False, 'error': 'Only completed or halted sales can be refunded.'}, status=400)

    # Check for an already-pending or approved refund request
    from .models import RefundRequest
    if RefundRequest.objects.filter(sale=sale, status='PENDING').exists():
        return JsonResponse({'success': False, 'error': 'A refund request for this sale is already pending approval.'}, status=400)
    if RefundRequest.objects.filter(sale=sale, status='APPROVED', payout_status='PENDING').exists():
        return JsonResponse({'success': False, 'error': 'An approved refund request for this sale is already awaiting cashier disbursement.'}, status=400)

    try:
        data = json.loads(request.body) if request.body else {}
        reason = data.get('reason', 'Customer requested refund')
        returned_items_raw = data.get('returned_items', [])
    except Exception:
        reason = 'Customer requested refund'
        returned_items_raw = []

    validated_returns = []
    sale_items_map = {item.id: item for item in sale.items.select_related('product').all()}
    if returned_items_raw and isinstance(returned_items_raw, list):
        for entry in returned_items_raw:
            try:
                item_id = int(entry.get('sale_item_id') or entry.get('id'))
                ret_qty = Decimal(str(entry.get('return_qty', 0)))
            except (ValueError, TypeError, InvalidOperation):
                continue
            if ret_qty <= Decimal('0'):
                continue
            item = sale_items_map.get(item_id)
            if not item:
                continue
            disp_qty = item.dispatched_quantity if item.dispatched_quantity > Decimal('0') else (
                item.quantity if not tenant.use_strict_sales_workflow else Decimal('0')
            )
            if ret_qty > disp_qty:
                return JsonResponse({
                    'success': False,
                    'error': f"Cannot return {ret_qty} of '{item.product.name}' — only {disp_qty} dispatched."
                }, status=400)
            eff_price = (item.total / item.quantity).quantize(Decimal('0.01')) if item.quantity > Decimal('0') else item.unit_price
            ret_val = (eff_price * ret_qty).quantize(Decimal('0.01'))
            validated_returns.append({
                'sale_item_id': item.id,
                'product_id': item.product_id,
                'product_name': item.product.name,
                'sku': item.product.sku,
                'return_qty': str(ret_qty),
                'unit_price': str(eff_price),
                'return_val': str(ret_val),
            })

    if validated_returns:
        # Partial return of dispatched goods
        is_partial_return = True
        is_partial_halt = False
        total_return_val = sum(Decimal(it['return_val']) for it in validated_returns)
        kept_val = max(Decimal('0.00'), (sale.dispatched_total or sale.total) - total_return_val)
        if sale.amount_paid and sale.amount_paid > kept_val:
            refund_amount = min(total_return_val, sale.amount_paid - kept_val)
            debt_reduction_amount = total_return_val - refund_amount
        else:
            refund_amount = Decimal('0.00')
            debt_reduction_amount = total_return_val
        dispatched_total = kept_val

        if role_name in ['ACCOUNTANT', 'ADMIN']:
            try:
                with transaction.atomic():
                    refund_req = RefundRequest.objects.create(
                        tenant=tenant,
                        sale=sale,
                        requested_by=request.user,
                        reason=reason,
                        status='PENDING',
                        is_partial_halt=False,
                        is_partial_return=True,
                        refund_amount=refund_amount,
                        dispatched_total=dispatched_total,
                        debt_reduction_amount=debt_reduction_amount,
                        returned_items=validated_returns,
                    )
                    refund_req.approve(request.user)
                    if refund_req.payout_status == 'PENDING':
                        refund_req.execute_payout(request.user)
                return JsonResponse({'success': True, 'message': f'Sale {sale.sale_number} return processed successfully.'})
            except Exception as e:
                return JsonResponse({'success': False, 'error': str(e)}, status=400)
    elif role_name in ['ACCOUNTANT', 'ADMIN']:
        # Accountants and Admins execute full refund immediately
        try:
            with transaction.atomic():
                sale.refund(reason=reason, user=request.user)
            return JsonResponse({'success': True, 'message': f'Sale {sale.sale_number} successfully refunded.'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    elif (sale.is_halted or (sale.is_partially_dispatched and not sale.all_items_dispatched)) or (sale.amount_paid and sale.total and sale.amount_paid > sale.total):
        # Determine refund amount for partial halt refund
        is_partial_return = False
        dispatched_total = Decimal('0.00')
        for item in sale.items.all():
            if item.quantity > Decimal('0') and item.dispatched_quantity > Decimal('0'):
                effective_price = item.total / item.quantity
                dispatched_item_val = (effective_price * item.dispatched_quantity).quantize(Decimal('0.01'))
                dispatched_total += dispatched_item_val

        if dispatched_total <= Decimal('0.00') and sale.amount_paid and sale.total and sale.amount_paid > sale.total:
            dispatched_total = sale.total

        undispatched_total = max(Decimal('0.00'), sale.total - dispatched_total)
        has_dispatched_items = dispatched_total > Decimal('0.00')

        if sale.amount_paid > dispatched_total:
            refund_amount = sale.amount_paid - dispatched_total
            debt_reduction_amount = max(Decimal('0.00'), sale.total - sale.amount_paid)
        else:
            refund_amount = Decimal('0.00')
            debt_reduction_amount = undispatched_total

        is_partial_halt = has_dispatched_items
    else:
        # Full sale refund request (0 goods retained by customer)
        is_partial_halt = False
        is_partial_return = False
        refund_amount = sale.amount_paid or Decimal('0.00')
        dispatched_total = Decimal('0.00')
        debt_reduction_amount = max(Decimal('0.00'), sale.total - sale.amount_paid) if sale.amount_paid else sale.total

    # Shop Managers / Cashiers — create a pending RefundRequest
    try:
        with transaction.atomic():
            refund_req = RefundRequest.objects.create(
                tenant=tenant,
                sale=sale,
                requested_by=request.user,
                reason=reason,
                status='PENDING',
                is_partial_halt=is_partial_halt,
                is_partial_return=is_partial_return,
                returned_items=validated_returns if is_partial_return else None,
                refund_amount=refund_amount,
                dispatched_total=dispatched_total,
                debt_reduction_amount=debt_reduction_amount,
            )

            # Notify all accountants + admins in this tenant via in-app notification & bulletin
            from apps.notifications.services import notify_and_bulletin
            notify_and_bulletin(
                tenant=tenant,
                title=f"Refund Request Awaiting Approval: {refund_req.refund_number}",
                message=(
                    f"{request.user.get_full_name() or request.user.email} has requested a refund "
                    f"for Sale {sale.sale_number} ({tenant.currency_symbol}{refund_amount}) "
                    f"at {sale.shop.name}. "
                    f"Reason: {reason[:150]}"
                ),
                notification_type='REFUND_REQUEST',
                target_roles=['ACCOUNTANT', 'ADMIN', 'SHOP_MANAGER'],
                reference_type='RefundRequest',
                reference_id=refund_req.pk,
                created_by=request.user,
                exclude_users=[request.user],
                is_pinned=True
            )

        return JsonResponse({
            'success': True,
            'pending_approval': True,
            'refund_id': refund_req.pk,
            'refund_number': refund_req.refund_number,
            'refund_amount': str(refund_amount),
            'message': (
                f'Refund request {refund_req.refund_number} ({tenant.currency_symbol}{refund_amount:.2f}) submitted. '
                f'Awaiting accountant approval.'
            ),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def api_approve_refund(request, pk):
    """
    Approve a pending RefundRequest.
    Only Accountants and Admins can call this endpoint.
    """
    import json
    from .models import RefundRequest

    tenant = request.user.tenant
    role_name = request.user.role.name if request.user.role else None

    if role_name not in ['ACCOUNTANT', 'ADMIN']:
        return JsonResponse({'success': False, 'error': 'Only accountants and admins can approve refunds.'}, status=403)

    refund_req = get_object_or_404(RefundRequest, pk=pk, tenant=tenant)

    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        data = {}
    refund_method = data.get('refund_method')

    try:
        refund_req.approve(request.user, refund_method=refund_method)
        return JsonResponse({
            'success': True,
            'message': (
                f'Refund {refund_req.refund_number} approved. '
                f'Sale {refund_req.sale.sale_number} has been settled.'
            ),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def api_payout_refund(request, pk):
    """
    Cashier or Admin executes payout (Cash, MoMo, ECash) for an approved refund request.
    Only Cashiers and Admins can disburse refund payouts.
    Effectively executes fund deductions from cashier till / wallets and finalizes sale settlement.
    """
    import json
    from .models import RefundRequest, Shift
    from django.utils import timezone

    tenant = request.user.tenant
    role_name = request.user.role.name if request.user.role else None

    if tenant.use_strict_sales_workflow:
        if role_name != 'SHOP_CASHIER':
            return JsonResponse({'success': False, 'error': 'In strict sales mode, only cashiers can disburse refund payouts.'}, status=403)
    elif role_name not in ['SHOP_CASHIER', 'ADMIN']:
        return JsonResponse({'success': False, 'error': 'Only cashiers and admins can disburse refund payouts.'}, status=403)

    refund_req = get_object_or_404(RefundRequest, pk=pk, tenant=tenant)

    if refund_req.status != 'APPROVED':
        return JsonResponse({'success': False, 'error': 'Refund request must be approved before payout.'}, status=400)

    if refund_req.payout_status == 'PAID':
        return JsonResponse({'success': False, 'error': 'This refund has already been paid out.'}, status=400)

    if role_name == 'SHOP_CASHIER' and refund_req.sale.shop != request.user.location:
        return JsonResponse({'success': False, 'error': 'You can only process payouts for your own shop.'}, status=403)

    try:
        data = json.loads(request.body) if request.body else {}
    except Exception:
        data = {}

    payout_notes = data.get('payout_notes', '').strip()
    transaction_ref = data.get('transaction_reference', '').strip()

    # Find active open shift for this user at this shop
    active_shift = Shift.objects.filter(
        tenant=tenant,
        shop=refund_req.sale.shop,
        attendant=request.user,
        status='OPEN'
    ).first()

    # Execute payout: deducts fund sources and finalizes sale settlement
    try:
        refund_req.execute_payout(
            disburser=request.user,
            shift=active_shift,
            transaction_reference=transaction_ref,
            payout_notes=payout_notes
        )
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    cashier_name = request.user.get_full_name() or request.user.email
    return JsonResponse({
        'success': True,
        'message': f'Payout of {tenant.currency_symbol}{refund_req.refund_amount} ({refund_req.get_refund_method_display()}) recorded successfully.',
        'payout_number': refund_req.refund_number,
        'disbursed_by': cashier_name,
        'paid_at': refund_req.paid_at.strftime('%b %d, %Y %I:%M %p') if refund_req.paid_at else '',
    })


@login_required
@require_POST
def api_reject_refund(request, pk):
    """
    Reject a pending RefundRequest.
    Only Accountants and Admins can call this endpoint.
    """
    import json
    from .models import RefundRequest

    tenant = request.user.tenant
    role_name = request.user.role.name if request.user.role else None

    if role_name not in ['ACCOUNTANT', 'ADMIN']:
        return JsonResponse({'success': False, 'error': 'Only accountants and admins can reject refunds.'}, status=403)

    refund_req = get_object_or_404(RefundRequest, pk=pk, tenant=tenant)

    try:
        data = json.loads(request.body)
        rejection_reason = data.get('reason', '')
    except Exception:
        rejection_reason = request.POST.get('reason', '')

    try:
        refund_req.reject(request.user, rejection_reason)
        return JsonResponse({
            'success': True,
            'message': f'Refund request {refund_req.refund_number} rejected.',
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_POST
def api_cancel_refund(request, pk):
    """
    Cancel an approved RefundRequest before it has been disbursed.
    Only Accountants and Admins can call this endpoint.
    """
    import json
    from .models import RefundRequest

    tenant = request.user.tenant
    role_name = request.user.role.name if request.user.role else None

    if role_name not in ['ACCOUNTANT', 'ADMIN']:
        return JsonResponse({'success': False, 'error': 'Only accountants and admins can cancel refund approvals.'}, status=403)

    refund_req = get_object_or_404(RefundRequest, pk=pk, tenant=tenant)

    try:
        data = json.loads(request.body) if request.body else {}
        reason = data.get('reason', '').strip()
    except Exception:
        reason = request.POST.get('reason', '').strip()

    try:
        refund_req.cancel_approval(request.user, reason=reason)
        return JsonResponse({
            'success': True,
            'message': f'Approval for refund request {refund_req.refund_number} has been cancelled.',
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)



class RefundRequestListView(LoginRequiredMixin, ListView):
    """
    List refund requests.
    - Shop Managers: see requests for their shop only.
    - Accountants / Admins: see all tenant requests (pending first).
    """
    from .models import RefundRequest as _RefundRequest
    model = _RefundRequest
    template_name = 'sales/refund_request_list.html'
    context_object_name = 'refund_requests'
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN', 'AUDITOR', 'SHOP_CASHIER']:
            messages.error(request, 'You do not have permission to view refund requests.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        from .models import RefundRequest
        user = self.request.user
        role_name = user.role.name if user.role else None
        tenant = user.tenant

        qs = RefundRequest.objects.filter(tenant=tenant).select_related(
            'sale', 'sale__shop', 'sale__customer', 'requested_by', 'reviewed_by', 'paid_by', 'shift'
        )

        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and user.location:
            qs = qs.filter(sale__shop=user.location)

        # Filter by status
        status_filter = self.request.GET.get('status', '')
        if status_filter == 'PENDING_PAYOUT':
            qs = qs.filter(status='APPROVED', payout_status='PENDING')
        elif status_filter:
            qs = qs.filter(status=status_filter)

        # Filter by shop
        shop_id = self.request.GET.get('shop')
        if shop_id and role_name not in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            qs = qs.filter(sale__shop_id=shop_id)

        # Filter by attendant
        attendant_id = self.request.GET.get('attendant')
        if attendant_id:
            qs = qs.filter(sale__attendant_id=attendant_id)

        # Filter by date
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        if start_date:
            qs = qs.filter(created_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(created_at__date__lte=end_date)

        # Pending first, then newest
        from django.db.models import Case, When, IntegerField
        qs = qs.annotate(
            status_order=Case(
                When(status='PENDING', then=0),
                When(status='APPROVED', payout_status='PENDING', then=1),
                When(status='APPROVED', payout_status='PAID', then=2),
                When(status='REJECTED', then=3),
                When(status='CANCELLED', then=4),
                default=5,
                output_field=IntegerField(),
            )
        ).order_by('status_order', '-created_at')

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import RefundRequest, Shift
        from apps.core.models import Location, User
        tenant = self.request.user.tenant
        role_name = self.request.user.role.name if self.request.user.role else None

        context['role_name'] = role_name
        context['pending_count'] = RefundRequest.objects.filter(tenant=tenant, status='PENDING').count()
        context['selected_status'] = self.request.GET.get('status', '')
        context['refund_always_cash'] = tenant.refund_always_cash
        
        context['shops'] = Location.objects.filter(tenant=tenant, location_type='SHOP')
        active_shift = None
        if self.request.user.location:
            active_shift = Shift.objects.filter(tenant=tenant, shop=self.request.user.location, attendant=self.request.user, status='OPEN').first()
        if not active_shift:
            active_shift = Shift.objects.filter(tenant=tenant, attendant=self.request.user, status='OPEN').first()
        context['active_shift'] = active_shift
        context['cashier_effective_cash'] = active_shift.expected_cash if active_shift else None

        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and self.request.user.location:
            context['attendants'] = User.objects.filter(tenant=tenant, location=self.request.user.location, is_active=True).order_by('first_name', 'last_name')
        else:
            context['attendants'] = User.objects.filter(tenant=tenant, is_active=True).order_by('first_name', 'last_name')

        payout_qs = RefundRequest.objects.filter(tenant=tenant, status='APPROVED', payout_status='PENDING', refund_amount__gt=Decimal('0.00'))
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and self.request.user.location:
            payout_qs = payout_qs.filter(sale__shop=self.request.user.location)
        context['pending_payout_count'] = payout_qs.count()

        context['selected_shop'] = self.request.GET.get('shop', '')
        context['selected_attendant'] = self.request.GET.get('attendant', '')
        context['start_date'] = self.request.GET.get('start_date', '')
        context['end_date'] = self.request.GET.get('end_date', '')
            
        return context



# ============ API Views for POS ============

@login_required
def api_product_search(request):
    """Search products for POS - optimized with prefetch."""
    from django.db.models import Q, Prefetch
    
    query = request.GET.get('q', '')
    shop = request.user.location
    
    if not shop or shop.location_type != 'SHOP':
        return JsonResponse({'products': []})
    
    # Prefetch shop prices for THIS shop only
    shop_price_prefetch = Prefetch(
        'shop_prices',
        queryset=ShopPrice.objects.filter(location=shop, is_active=True),
        to_attr='current_shop_prices'
    )
    
    products = Product.objects.filter(
        tenant=request.user.tenant,
        is_active=True
    ).filter(
        Q(name__icontains=query) | 
        Q(sku__icontains=query) |
        Q(barcode__icontains=query)
    ).prefetch_related(shop_price_prefetch)[:20]
    
    results = []
    for product in products:
        shop_prices = getattr(product, 'current_shop_prices', [])
        if shop_prices:
            shop_price = shop_prices[0]
            results.append({
                'id': product.pk,
                'name': product.name,
                'sku': product.sku,
                'price': str(shop_price.selling_price),
                'unit': product.unit_of_measure,
            })
    
    return JsonResponse({'products': results})


def _validate_stock_for_cart(cart_items, shop, user):
    """
    Validate that stock is available according to shop settings and tenant rules.
    If allow_negative_stock is True, returns None (allowed).
    If allow_negative_stock is False:
      - Shop A (Unique items, show_items_with_physical_stock_in_pos=False in strict workflow):
        Requires qty_needed <= (physical_stock - pending_dispatch).
      - Shop B (Replaceable items, show_items_with_physical_stock_in_pos=True, or non-strict):
        Requires qty_needed <= physical_stock (even if physical - pending_dispatch < 0).
    Returns None if valid, or an error string if invalid.
    """
    from decimal import Decimal
    from django.db.models import Sum, F, Q
    from apps.inventory.models import InventoryLedger

    tenant = user.tenant
    if tenant.allow_negative_stock:
        return None

    shop_settings_obj = getattr(shop, 'shop_settings', None)
    if not shop_settings_obj and shop:
        try:
            shop_settings_obj = ShopSettings.objects.get(shop=shop)
        except ShopSettings.DoesNotExist:
            shop_settings_obj = None

    show_phys = bool(shop_settings_obj and shop_settings_obj.show_items_with_physical_stock_in_pos)
    is_strict = bool(tenant.use_strict_sales_workflow)

    for item in cart_items:
        product_id = item.get('product_id') or item.get('id')
        product = Product.objects.get(pk=product_id)
        qty_needed = Decimal(str(item['quantity']))

        phys_stock = InventoryLedger.objects.filter(
            tenant=tenant,
            location=shop,
            product=product
        ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')

        if is_strict and not show_phys:
            # Shop A: Unique goods (available stock = physical - pending dispatch)
            undispatched = SaleItem.objects.filter(
                sale__tenant=tenant,
                sale__shop=shop,
                product=product,
                sale__status__in=['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED', 'COMPLETED'],
                dispatched_quantity__lt=F('quantity')
            ).exclude(
                Q(sale__status__in=['VOIDED', 'REFUNDED']) | Q(sale__is_halted=True)
            ).aggregate(
                total=Sum(F('quantity') - F('dispatched_quantity'))
            )['total'] or Decimal('0')

            avail = max(Decimal('0'), phys_stock - undispatched)
            if qty_needed > avail:
                return f"Cannot sell/invoice {qty_needed} of '{product.name}'. Only {avail} available ({undispatched} already sold & pending dispatch)."
        else:
            # Shop B: Replaceable goods (allowed as long as physical stock in inventory >= qty_needed)
            if qty_needed > phys_stock:
                return f"Cannot sell/invoice {qty_needed} of '{product.name}'. Only {max(Decimal('0'), phys_stock)} physically in stock."

    return None


@login_required
@require_POST
def api_complete_sale(request):
    """Complete a sale via AJAX."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    cart_items  = data.get('items', []) or data.get('cart', [])
    payment_method = data.get('payment_method', 'CASH')
    
    if payment_method == 'ECASH':
        return JsonResponse({'error': 'E-Cash payments must be verified via the ECASH endpoint.'}, status=400)
        
    # Ensure payment_method is valid - handle empty string or invalid values
    valid_payment_methods = ['CASH', 'CREDIT', 'MIXED', 'PAYMENT_ON_ACCOUNT', 'MOMO', 'PENDING_INVOICE']
    if not payment_method or payment_method not in valid_payment_methods:
        payment_method = 'CASH'
    amount_paid = Decimal(str(data.get('amount_paid', 0)))
    discount_amount = Decimal(str(data.get('discount_amount', 0) or data.get('discount', 0)))
    discount_reason = data.get('discount_reason', '')
    paystack_ref = data.get('paystack_reference', '')
    customer_id = data.get('customer_id')
    is_payment_on_account = data.get('is_payment_on_account', False)
    # Cashier workflow fields (used by PENDING_INVOICE path only)
    customer_name  = data.get('customer_name', '').strip()
    customer_phone = data.get('customer_phone', '').strip()
    
    shop = request.user.location
    
    if not shop or shop.location_type != 'SHOP':
        return JsonResponse({'error': 'No shop assigned'}, status=400)
    
    # Get customer if specified
    customer = None
    if customer_id:
        from apps.customers.models import Customer, CustomerTransaction
        customer = Customer.objects.filter(pk=customer_id, tenant=request.user.tenant).first()

    # ── PENDING INVOICE path (Cashier Workflow & Strict Sales Workflow) ────────
    # Creates a PENDING sale without deducting stock. The cashier will later
    # complete the sale and trigger stock deduction at that point.
    if payment_method == 'PENDING_INVOICE':
        if not cart_items:
            return JsonResponse({'error': 'Cart is empty'}, status=400)
        if not customer_name or not customer_phone:
            return JsonResponse({'error': 'Customer name and phone are required for invoices'}, status=400)

        stock_err = _validate_stock_for_cart(cart_items, shop, request.user)
        if stock_err:
            return JsonResponse({'error': stock_err}, status=400)

        shift = Shift.objects.filter(
            tenant=request.user.tenant,
            shop=shop,
            attendant=request.user,
            status='OPEN'
        ).first()

        try:
            with transaction.atomic():
                sale = Sale.objects.create(
                    tenant=request.user.tenant,
                    shop=shop,
                    attendant=request.user,
                    shift=shift,
                    customer=customer,
                    payment_method='PENDING_INVOICE',
                    status='PENDING',
                    discount_amount=discount_amount,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                )
                for item in cart_items:
                    product = Product.objects.get(pk=item['product_id'])
                    SaleItem.objects.create(
                        tenant=request.user.tenant,
                        sale=sale,
                        product=product,
                        quantity=Decimal(str(item['quantity'])),
                        unit_price=Decimal(str(item['unit_price'])),
                    )
                sale.calculate_totals()
                return JsonResponse({
                    'success': True,
                    'sale_id': sale.pk,
                    'sale_number': sale.sale_number,
                    'total': str(sale.total),
                })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    # ── Handle payment on account (no cart items, just payment to customer) ──
    if is_payment_on_account or payment_method == 'PAYMENT_ON_ACCOUNT':
        if not customer:
            return JsonResponse({'error': 'Customer required for payment on account'}, status=400)
        if amount_paid <= 0:
            return JsonResponse({'error': 'Payment amount must be positive'}, status=400)
        
        # Get or check shift for cash tracking
        shift = Shift.objects.filter(
            tenant=request.user.tenant,
            shop=shop,
            attendant=request.user,
            status='OPEN'
        ).first()
        
        try:
            with transaction.atomic():
                from apps.customers.models import CustomerTransaction
                
                balance_before = customer.current_balance
                customer.current_balance -= amount_paid  # Payment reduces balance
                customer.save()
                
                # Create transaction record
                txn = CustomerTransaction.objects.create(
                    tenant=request.user.tenant,
                    customer=customer,
                    transaction_type='CREDIT',  # Credit = Payment received
                    amount=amount_paid,
                    description=f"Payment on account ({payment_method})",
                    reference_id=f"POA-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                    balance_before=balance_before,
                    balance_after=customer.current_balance,
                    performed_by=request.user
                )
                
                # Note: Cash payments tracked via CustomerTransaction records
                # Shift totals are computed from Sale records automatically
                
                return JsonResponse({
                    'success': True,
                    'message': f'Payment of {amount_paid} recorded',
                    'new_balance': str(customer.current_balance),
                    'transaction_id': txn.pk,
                })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    
    # Normal sale processing
    if not cart_items:
        return JsonResponse({'error': 'Cart is empty'}, status=400)

    stock_err = _validate_stock_for_cart(cart_items, shop, request.user)
    if stock_err:
        return JsonResponse({'error': stock_err}, status=400)

    # Get open shift
    shift = Shift.objects.filter(
        tenant=request.user.tenant,
        shop=shop,
        attendant=request.user,
        status='OPEN'
    ).first()
    
    try:
        with transaction.atomic():
            # Create sale
            sale = Sale.objects.create(
                tenant=request.user.tenant,
                shop=shop,
                attendant=request.user,
                shift=shift,
                customer=customer,
                payment_method=payment_method,
                discount_amount=discount_amount,
                discount_reason=discount_reason,
            )
            
            # Add items
            for item in cart_items:
                product = Product.objects.get(pk=item['product_id'])
                quantity = Decimal(str(item['quantity']))
                unit_price = Decimal(str(item['unit_price']))
                
                SaleItem.objects.create(
                    tenant=request.user.tenant,
                    sale=sale,
                    product=product,
                    quantity=quantity,
                    unit_price=unit_price,
                )
            
            # Calculate totals
            sale.calculate_totals()

            if payment_method == 'MOMO':
                shop_settings = getattr(shop, 'shop_settings', None)
                if not shop_settings and shop:
                    try:
                        from apps.sales.models import ShopSettings
                        shop_settings = ShopSettings.objects.get(shop=shop)
                    except ShopSettings.DoesNotExist:
                        shop_settings = None
                if shop_settings and shop_settings.disallow_manual_momo_amount:
                    amount_paid = sale.total
            
            # Handle overpayment for customer (reduces their balance)
            if customer and amount_paid > sale.total:
                from apps.customers.models import CustomerTransaction
                overpayment = amount_paid - sale.total
                
                balance_before = customer.current_balance
                customer.current_balance -= overpayment  # Overpayment reduces balance
                customer.save()
                
                CustomerTransaction.objects.create(
                    tenant=request.user.tenant,
                    customer=customer,
                    transaction_type='CREDIT',
                    amount=overpayment,
                    description=f"Overpayment from sale {sale.sale_number}",
                    reference_id=sale.sale_number,
                    balance_before=balance_before,
                    balance_after=customer.current_balance,
                    performed_by=request.user
                )
                
                # For the sale, record only the total as paid
                sale.complete(sale.total, payment_method, paystack_ref)
            else:
                # Complete sale (handles partial payments)
                sale.complete(amount_paid, payment_method, paystack_ref)
            
            return JsonResponse({
                'success': True,
                'sale_id': sale.pk,
                'sale_number': sale.sale_number,
                'total': str(sale.total),
                'change': str(sale.change_given),
            })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_void_sale(request, pk):
    """Void a sale."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    sale = get_object_or_404(
        Sale,
        pk=pk,
        tenant=request.user.tenant
    )
    
    reason = request.POST.get('reason', '')
    
    try:
        sale.void(reason)
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_pay_invoice(request, pk):
    """Complete payment for a pending invoice (Cashier Workflow)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
        
    sale = get_object_or_404(
        Sale,
        pk=pk,
        tenant=request.user.tenant,
        status='PENDING'
    )
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
        
    payment_method = data.get('payment_method', 'CASH')
    
    if payment_method == 'ECASH':
        return JsonResponse({'error': 'E-Cash payments must be verified via the ECASH endpoint.'}, status=400)
        
    valid_payment_methods = ['CASH', 'CREDIT', 'MIXED', 'PAYMENT_ON_ACCOUNT', 'MOMO']
    if not payment_method or payment_method not in valid_payment_methods:
        payment_method = 'CASH'
        
    amount_paid = Decimal(str(data.get('amount_paid', 0)))
    paystack_ref = data.get('paystack_reference', '')

    if payment_method == 'MOMO':
        shop_settings = getattr(sale.shop, 'shop_settings', None)
        if not shop_settings and sale.shop:
            try:
                from apps.sales.models import ShopSettings
                shop_settings = ShopSettings.objects.get(shop=sale.shop)
            except ShopSettings.DoesNotExist:
                shop_settings = None
        if shop_settings and shop_settings.disallow_manual_momo_amount:
            amount_paid = sale.total
    
    try:
        with transaction.atomic():
            sale.complete(amount_paid, payment_method, paystack_ref, cashier=request.user)
            
            # Notify Shop Manager if using strict sales workflow
            if request.user.tenant.use_strict_sales_workflow:
                from apps.notifications.models import Notification
                from apps.core.models import User
                shop_managers = User.objects.filter(
                    tenant=request.user.tenant,
                    location=sale.shop,
                    role__name='SHOP_MANAGER',
                    is_active=True
                )
                for manager in shop_managers:
                    Notification.objects.create(
                        tenant=request.user.tenant,
                        user=manager,
                        title='Invoice Paid',
                        message=f'Invoice {sale.sale_number} has been paid and is ready for dispatch.',
                        notification_type='INVOICE_PAID',
                        reference_type='Sale',
                        reference_id=sale.id
                    )
            
        return JsonResponse({
            'success': True,
            'sale_id': sale.pk,
            'sale_number': sale.sale_number,
            'total': str(sale.total),
            'change': str(sale.change_given),
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_dispatch_sale(request, pk):
    """
    Dispatch goods for a sale (full or partial) in the Strict Sales Workflow.

    POST body (JSON):
    {
        "items": [
            {"sale_item_id": 12, "qty": 3},
            {"sale_item_id": 13, "qty": 1}
        ]
    }
    If "items" is omitted or empty, all remaining quantities are dispatched.
    """
    import json
    from decimal import Decimal, InvalidOperation
    from django.db.models import F, Sum
    from apps.inventory.models import Batch, InventoryLedger

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    role_name = request.user.role.name if request.user.role else None
    if role_name not in ['SHOP_MANAGER', 'ADMIN']:
        return JsonResponse({'error': 'Only shop managers and admins can dispatch goods.'}, status=403)

    sale = get_object_or_404(
        Sale,
        pk=pk,
        tenant=request.user.tenant,
        status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']
    )

    if sale.is_dispatched:
        return JsonResponse({'error': 'This sale has already been fully dispatched.'}, status=400)

    from .models import RefundRequest
    if RefundRequest.objects.filter(sale=sale, status='PENDING').exists():
        return JsonResponse({'error': 'Cannot dispatch goods while a refund or halt request is pending Accountant approval.'}, status=400)

    # Parse requested dispatch quantities
    try:
        body = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        body = {}

    requested_items = body.get('items', [])  # [{"sale_item_id": X, "qty": Y}, ...]

    # Build a map of item_id -> qty_to_dispatch
    if requested_items:
        dispatch_map = {}
        for entry in requested_items:
            try:
                item_id = int(entry['sale_item_id'])
                qty = Decimal(str(entry['qty']))
                if qty > 0:
                    dispatch_map[item_id] = qty
            except (KeyError, ValueError, InvalidOperation):
                continue
    else:
        # Dispatch all remaining quantities
        dispatch_map = {
            item.id: item.remaining_quantity
            for item in sale.items.all()
            if item.remaining_quantity > 0
        }

    if not dispatch_map:
        return JsonResponse({'error': 'No items to dispatch.'}, status=400)

    try:
        with transaction.atomic():
            sale_items = {item.id: item for item in sale.items.select_related('product', 'batch').all()}

            for item_id, qty_to_dispatch in dispatch_map.items():
                item = sale_items.get(item_id)
                if not item:
                    raise ValueError(f"Sale item {item_id} not found on this sale.")

                remaining = item.remaining_quantity
                if qty_to_dispatch > remaining:
                    raise ValueError(
                        f"Cannot dispatch {qty_to_dispatch} of '{item.product.name}' "
                        f"— only {remaining} remaining."
                    )

                if not sale.tenant.allow_negative_stock:
                    current_physical = InventoryLedger.objects.filter(
                        tenant=sale.tenant,
                        location=sale.shop,
                        product=item.product
                    ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')
                    if current_physical < qty_to_dispatch:
                        raise ValueError(
                            f"Cannot dispatch {qty_to_dispatch} of '{item.product.name}'. "
                            f"Only {max(Decimal('0'), current_physical)} physically available in shop inventory. "
                            f"Please receive new stock before dispatching."
                        )

                # Resolve batch (FEFO)
                batch = item.batch
                if not batch:
                    batch = Batch.objects.filter(
                        tenant=sale.tenant,
                        product=item.product,
                        location=sale.shop,
                        status='AVAILABLE',
                        current_quantity__gt=0,
                    ).order_by('expiry_date', 'created_at').first()
                    if batch:
                        item.batch = batch

                actual_cost = batch.unit_cost if batch and batch.unit_cost else Decimal('0')
                if item.unit_cost == Decimal('0') and actual_cost > 0:
                    item.unit_cost = actual_cost

                # Deduct inventory for the dispatched quantity
                InventoryLedger.objects.create(
                    tenant=sale.tenant,
                    product=item.product,
                    batch=item.batch,
                    location=sale.shop,
                    transaction_type='SALE',
                    quantity=-qty_to_dispatch,
                    unit_cost=actual_cost,
                    reference_type='Sale',
                    reference_id=sale.pk,
                    notes=f"Partial dispatch {qty_to_dispatch}/{item.quantity} — Sale {sale.sale_number}",
                    created_by=request.user
                )

                # Update dispatched quantity on the item
                item.dispatched_quantity += qty_to_dispatch
                item.save(update_fields=['dispatched_quantity', 'unit_cost', 'batch'])

            # Re-check: is everything now dispatched?
            fully_dispatched = not sale.items.filter(
                dispatched_quantity__lt=F('quantity')
            ).exists()

            if fully_dispatched:
                if sale.status in ['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']:
                    sale.status = 'COMPLETED'
                sale.is_dispatched = True
                sale.dispatched_at = timezone.now()
                sale.dispatched_by = request.user
                sale.save()
                status_msg = 'All goods dispatched. Sale completed.'
            else:
                # Partially dispatched — change status to PARTIALLY_DISPATCHED
                sale.status = 'PARTIALLY_DISPATCHED'
                sale.dispatched_by = request.user
                sale.dispatched_at = timezone.now()
                sale.save(update_fields=['status', 'dispatched_by', 'dispatched_at'])
                status_msg = 'Partial dispatch recorded successfully.'

        from django.urls import reverse
        return JsonResponse({
            'success': True,
            'sale_id': sale.pk,
            'sale_number': sale.sale_number,
            'fully_dispatched': fully_dispatched,
            'message': status_msg,
            'waybill_url': reverse('sales:sale_waybill', args=[sale.pk]),
        })
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_halt_and_request_refund(request, pk):
    """
    Halt further dispatch of a partially dispatched sale in Strict Sales Workflow,
    and submit a partial refund / debt reduction request for Accountant approval.
    Only Shop Managers and Admins can initiate this.
    """
    import json
    from decimal import Decimal
    from django.db.models import F, Sum
    from .models import RefundRequest

    tenant = request.user.tenant
    if not tenant.use_strict_sales_workflow:
        return JsonResponse({'error': 'Strict sales workflow is not enabled for this tenant.'}, status=400)

    role_name = request.user.role.name if request.user.role else None
    if role_name not in ['SHOP_MANAGER', 'ADMIN']:
        return JsonResponse({'error': 'Only shop managers and admins can halt dispatches.'}, status=403)

    sale = get_object_or_404(
        Sale,
        pk=pk,
        tenant=tenant,
        status__in=['PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']
    )

    if role_name == 'SHOP_MANAGER' and sale.shop != request.user.location:
        return JsonResponse({'error': 'You can only manage sales for your own shop.'}, status=403)

    if RefundRequest.objects.filter(sale=sale, status='PENDING').exists():
        return JsonResponse({'error': 'A refund request for this sale is already pending approval.'}, status=400)

    # Check if there are undispatched items to halt
    has_undispatched = sale.items.filter(dispatched_quantity__lt=F('quantity')).exists()
    if not has_undispatched:
        return JsonResponse({'error': 'All items in this sale have already been dispatched.'}, status=400)

    try:
        body = json.loads(request.body) if request.body else {}
    except Exception:
        body = {}
    reason = body.get('reason', 'Customer halted remaining dispatch').strip() or 'Customer halted remaining dispatch'

    # Calculate dispatched value vs undispatched value
    dispatched_total = Decimal('0.00')
    for item in sale.items.all():
        if item.quantity > Decimal('0') and item.dispatched_quantity > Decimal('0'):
            effective_price = item.total / item.quantity
            dispatched_item_val = (effective_price * item.dispatched_quantity).quantize(Decimal('0.01'))
            dispatched_total += dispatched_item_val

    undispatched_total = max(Decimal('0.00'), sale.total - dispatched_total)

    # Determine cash refund vs debt reduction
    if sale.amount_paid > dispatched_total:
        refund_amount = sale.amount_paid - dispatched_total
        debt_reduction_amount = max(Decimal('0.00'), sale.total - sale.amount_paid)
    else:
        refund_amount = Decimal('0.00')
        debt_reduction_amount = undispatched_total

    try:
        with transaction.atomic():
            sale.halt_dispatch(user=request.user, reason=reason)

            from apps.notifications.services import notify_and_bulletin
            notify_and_bulletin(
                tenant=tenant,
                title=f"Dispatch Halted: Sale {sale.sale_number}",
                message=(
                    f"Dispatch for Sale {sale.sale_number} at {sale.shop.name} was halted by "
                    f"{request.user.get_full_name() or request.user.email}. "
                    f"Dispatched Total: {tenant.currency_symbol}{dispatched_total}, "
                    f"Undispatched Remaining: {tenant.currency_symbol}{undispatched_total}. "
                    f"Reason: {reason[:150]}"
                ),
                notification_type='REFUND_REQUEST',
                target_roles=['ACCOUNTANT', 'ADMIN', 'SHOP_CASHIER', 'SHOP_MANAGER'],
                reference_type='Sale',
                reference_id=sale.pk,
                created_by=request.user,
                exclude_users=[request.user],
                is_pinned=True
            )

        return JsonResponse({
            'success': True,
            'status': sale.status,
            'is_halted': True,
            'dispatched_total': str(dispatched_total),
            'undispatched_total': str(undispatched_total),
            'message': f'Dispatch halted for sale {sale.sale_number}. Cashier can now request refund or shop manager can resume.',
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_resume_dispatch(request, pk):
    """
    Resume a halted sale dispatch in Strict Sales Workflow.
    Allows Cashiers, Shop Managers, and Admins to reopen the sale for dispatch
    if the Shop Manager halted it by mistake.
    """
    import json
    from django.core.exceptions import ValidationError

    tenant = request.user.tenant
    if not tenant.use_strict_sales_workflow:
        return JsonResponse({'error': 'Strict sales workflow is not enabled for this tenant.'}, status=400)

    role_name = request.user.role.name if request.user.role else None
    if role_name not in ['SHOP_CASHIER', 'SHOP_MANAGER', 'ADMIN']:
        return JsonResponse({'error': 'Only cashiers, shop managers, and admins can resume sales.'}, status=403)

    sale = get_object_or_404(
        Sale,
        pk=pk,
        tenant=tenant
    )

    if role_name in ['SHOP_CASHIER', 'SHOP_MANAGER'] and sale.shop != request.user.location:
        return JsonResponse({'error': 'You can only resume sales for your own shop.'}, status=403)

    try:
        body = json.loads(request.body) if request.body else {}
    except Exception:
        body = {}
    reason = body.get('reason', 'Resumed by cashier').strip() or 'Resumed by cashier'

    try:
        with transaction.atomic():
            sale.resume_dispatch(user=request.user, reason=reason)

            from apps.notifications.services import notify_and_bulletin
            notify_and_bulletin(
                tenant=tenant,
                title=f"Dispatch Resumed: Sale {sale.sale_number}",
                message=(
                    f"Dispatch for Sale {sale.sale_number} at {sale.shop.name} has been resumed by "
                    f"{request.user.get_full_name() or request.user.email}."
                ),
                notification_type='SYSTEM',
                target_roles=['SHOP_MANAGER', 'SHOP_CASHIER', 'ADMIN'],
                reference_type='Sale',
                reference_id=sale.pk,
                created_by=request.user,
                exclude_users=[request.user]
            )

        return JsonResponse({
            'success': True,
            'message': f'Sale {sale.sale_number} has been resumed and returned to Pending Dispatch.',
            'status': sale.status,
            'is_dispatched': sale.is_dispatched,
            'sale_number': sale.sale_number,
        })
    except ValidationError as e:
        return JsonResponse({'error': e.message if hasattr(e, 'message') else str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_customer_credit_payout(request, pk):
    """
    Cashier dispenses cash to a registered customer who has available credit (store credit).
    Reduces customer credit balance (brings balance closer to 0) and records cash payout on shift.
    Works for both standard and strict workflows.
    """
    import json
    from decimal import Decimal, InvalidOperation
    from apps.customers.models import Customer, CustomerTransaction

    tenant = request.user.tenant
    role_name = request.user.role.name if request.user.role else None

    if tenant.use_strict_sales_workflow:
        allowed_roles = ['SHOP_CASHIER', 'ADMIN', 'ACCOUNTANT']
    else:
        allowed_roles = ['SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN']

    if role_name not in allowed_roles:
        msg = f'Your role ({role_name}) is not permitted to disburse customer credit payouts under the current workflow.'
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type):
            return JsonResponse({'success': False, 'error': msg}, status=403)
        from django.contrib import messages
        messages.error(request, msg)
        return redirect('customers:customer_detail', pk=pk)

    customer = get_object_or_404(Customer, pk=pk, tenant=tenant)

    # In our database, negative current_balance means the customer has credit with the store.
    if customer.current_balance >= Decimal('0.00'):
        return JsonResponse({
            'success': False,
            'error': f'Customer {customer.name} has no available credit balance to withdraw (Current balance: {tenant.currency_symbol}{customer.current_balance}).'
        }, status=400)

    available_credit = abs(customer.current_balance)

    try:
        body = json.loads(request.body) if request.body else {}
    except Exception:
        body = request.POST

    try:
        amount = Decimal(str(body.get('amount', '0')))
    except (InvalidOperation, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid payout amount.'}, status=400)

    if amount <= Decimal('0.00'):
        return JsonResponse({'success': False, 'error': 'Payout amount must be greater than zero.'}, status=400)

    if amount > available_credit:
        return JsonResponse({
            'success': False,
            'error': f'Payout amount ({tenant.currency_symbol}{amount}) exceeds available credit ({tenant.currency_symbol}{available_credit}).'
        }, status=400)

    notes = body.get('notes', '').strip()

    try:
        with transaction.atomic():
            balance_before = customer.current_balance
            customer.current_balance += amount  # increases balance (less negative)
            customer.save()

            txn_desc = f"Credit payout to customer (CASH){': ' + notes if notes else ''}"
            txn = CustomerTransaction.objects.create(
                tenant=tenant,
                customer=customer,
                transaction_type='DEBIT',
                amount=amount,
                description=txn_desc,
                reference_id=f"CPAY-{customer.pk}-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                balance_before=balance_before,
                balance_after=customer.current_balance,
                performed_by=request.user
            )

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type):
            return JsonResponse({
                'success': True,
                'transaction_id': txn.pk,
                'amount_paid': str(amount),
                'new_balance': str(customer.current_balance),
                'remaining_credit': str(abs(customer.current_balance) if customer.current_balance < Decimal('0.00') else Decimal('0.00')),
                'message': f'Credit payout of {tenant.currency_symbol}{amount} to {customer.name} completed successfully.'
            })
        from django.contrib import messages
        messages.success(request, f'Credit payout of {tenant.currency_symbol}{amount} to {customer.name} completed successfully.')
        return redirect('customers:customer_detail', pk=customer.pk)
    except Exception as e:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type):
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        from django.contrib import messages
        messages.error(request, str(e))
        return redirect('customers:customer_detail', pk=customer.pk)




class ShopSalesReportView(LoginRequiredMixin, View):
    """
    Shop Manager view showing sales breakdown by attendant.
    """
    template_name = 'sales/shop_sales_report.html'
    
    def get(self, request):
        from django.db.models import Sum, Count, Q
        from django.utils import timezone
        from datetime import timedelta, datetime
        
        user = request.user
        role_name = user.role.name if user.role else None
        
        # Allow shop managers, accountants, auditors, and admin to access
        if role_name not in ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            messages.error(request, 'You do not have permission to view this report.')
            return redirect('core:dashboard')
        
        # Get the shop
        shop = user.location
        if not shop and role_name in ['ADMIN', 'ACCOUNTANT', 'AUDITOR']:
            # Admin/Accountant/Auditor can select any shop
            shop_id = request.GET.get('shop')
            if shop_id:
                shop = Location.objects.filter(
                    tenant=user.tenant, pk=shop_id, location_type='SHOP'
                ).first()
        
        context = {'shop': shop}
        
        if not shop or shop.location_type != 'SHOP':
            context['shops'] = Location.objects.filter(
                tenant=user.tenant, location_type='SHOP', is_active=True
            )
            return render(request, self.template_name, context)
        
        # Get attendants for this shop (for filter dropdown)
        from apps.core.models import User as TenantUser
        context['attendants'] = TenantUser.objects.filter(
            tenant=user.tenant,
            location=shop,
            role__name__in=['SHOP_ATTENDANT', 'SHOP_MANAGER'],
            is_active=True
        ).order_by('first_name', 'email')
        
        # Date range filter - support custom dates or presets
        today = timezone.now().date()
        date_from_str = request.GET.get('date_from')
        date_to_str = request.GET.get('date_to')
        date_range = request.GET.get('range', 'month')  # Default to month for better visibility
        
        # Parse custom dates if provided
        if date_from_str and date_to_str:
            try:
                date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
                date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
                context['date_range_label'] = f'{date_from.strftime("%b %d")} - {date_to.strftime("%b %d, %Y")}'
                date_range = 'custom'
            except ValueError:
                date_from = today - timedelta(days=30)
                date_to = today
                context['date_range_label'] = 'Last 30 Days'
        elif date_range == 'today':
            date_from = today
            date_to = today
            context['date_range_label'] = 'Today'
        elif date_range == 'week':
            date_from = today - timedelta(days=7)
            date_to = today
            context['date_range_label'] = 'Last 7 Days'
        else:  # month (default)
            date_from = today - timedelta(days=30)
            date_to = today
            context['date_range_label'] = 'Last 30 Days'
        
        context['current_range'] = date_range
        context['date_from'] = date_from
        context['date_to'] = date_to
        
        # Attendant filter
        attendant_id = request.GET.get('attendant')
        context['selected_attendant'] = attendant_id
        
        # Payment method filter
        payment_filter = request.GET.get('payment')
        context['selected_payment'] = payment_filter
        
        # Build base sales filter
        sales_filter = Q(
            tenant=user.tenant,
            shop=shop,
            status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'],
            created_at__date__gte=date_from,
            created_at__date__lte=date_to
        )
        
        # Apply attendant filter if selected
        if attendant_id:
            sales_filter &= Q(attendant_id=attendant_id)
        
        # Apply payment method filter if selected
        if payment_filter:
            sales_filter &= Q(payment_method=payment_filter)
        
        # Build sale items filter (for products)
        items_filter = {
            'sale__tenant': user.tenant,
            'sale__shop': shop,
            'sale__status__in': ['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'],
            'sale__created_at__date__gte': date_from,
            'sale__created_at__date__lte': date_to,
        }
        if attendant_id:
            items_filter['sale__attendant_id'] = attendant_id
        if payment_filter:
            items_filter['sale__payment_method'] = payment_filter
        
        # Get sales by attendant
        attendant_stats = Sale.objects.filter(sales_filter).values(
            'attendant__id',
            'attendant__first_name',
            'attendant__last_name',
            'attendant__email'
        ).annotate(
            total_sales=Count('id'),
            total_revenue=Sum('total'),
            cash_amount=Sum('total', filter=Q(payment_method='CASH')),
            ecash_amount=Sum('total', filter=Q(payment_method='ECASH')),
        ).order_by('-total_revenue')
        
        context['attendant_stats'] = attendant_stats
        
        # Get shop totals
        context['shop_totals'] = Sale.objects.filter(sales_filter).aggregate(
            total_sales=Count('id'),
            total_revenue=Sum('total'),
            cash_total=Sum('total', filter=Q(payment_method='CASH')),
            ecash_total=Sum('total', filter=Q(payment_method='ECASH')),
        )
        
        # Top 10 products sold
        context['top_products'] = SaleItem.objects.filter(
            **items_filter
        ).values('product__id', 'product__name').annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total')
        ).order_by('-revenue')[:10]
        
        # Full product sales breakdown (all products)
        all_products = SaleItem.objects.filter(
            **items_filter
        ).values('product__id', 'product__name').annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total')
        ).order_by('product__name')
        
        context['all_products'] = all_products
        context['all_products_total_qty'] = sum(p['qty_sold'] or 0 for p in all_products)
        context['all_products_total_revenue'] = sum(p['revenue'] or 0 for p in all_products)
        
        hide_zero_sales = request.GET.get('hide_zero_sales') == 'true'
        context['hide_zero_sales'] = hide_zero_sales

        from decimal import Decimal
        # Get raw sales by day from DB
        raw_sales_by_day = Sale.objects.filter(sales_filter).values(
            'created_at__date'
        ).annotate(
            revenue=Sum('total'),
            count=Count('id')
        ).order_by('created_at__date')
        
        if hide_zero_sales:
            sales_by_day = list(raw_sales_by_day)
        else:
            # Build dictionary for quick lookup
            sales_dict = {item['created_at__date']: item for item in raw_sales_by_day}
            
            # Generate continuous days for the selected range
            sales_by_day = []
            current_date = date_from
            while current_date <= date_to:
                if current_date in sales_dict:
                    sales_by_day.append(sales_dict[current_date])
                else:
                    sales_by_day.append({
                        'created_at__date': current_date,
                        'revenue': Decimal('0'),
                        'count': 0
                    })
                current_date += timedelta(days=1)
            
        # Reverse so newest is first for the table (chart in JS will reverse it back)
        sales_by_day.reverse()
        context['sales_by_day'] = sales_by_day
        
        # Price history for this shop
        from apps.inventory.models import ShopPrice
        context['price_history'] = ShopPrice.objects.filter(
            tenant=user.tenant,
            location=shop
        ).select_related('product').order_by('-created_at')[:20]
        
        return render(request, self.template_name, context)


# ============ E-Cash Payment API Views ============

@login_required
@require_POST
def initialize_ecash_payment(request):
    """
    Initialize an e-cash payment via Paystack.
    Creates a pending sale and returns Paystack configuration.
    """
    try:
        data = json.loads(request.body)
        user = request.user
        tenant = user.tenant
        
        # Get shop
        shop = user.location
        if not shop or shop.location_type != 'SHOP':
            from apps.core.models import Location
            shop = Location.objects.filter(
                tenant=tenant,
                location_type='SHOP',
                is_active=True
            ).first()
        
        if not shop:
            return JsonResponse({
                'success': False,
                'error': 'No shop location configured.'
            }, status=400)
        
        provider_config_id = data.get('provider_config_id')
        
        # Get active payment provider
        from apps.payments.services.paystack import get_payment_provider
        provider = get_payment_provider(tenant, shop=shop, config_id=provider_config_id)
        
        if not provider:
            return JsonResponse({
                'success': False,
                'error': 'Selected e-cash payment provider is not configured for this shop. Please contact admin.'
            }, status=400)
        
        items = data.get('items', [])
        discount = Decimal(str(data.get('discount_amount', 0)))
        customer_id = data.get('customer_id')
        total = Decimal(str(data.get('total', 0)))
        is_payment_on_account = data.get('is_payment_on_account', False)
        existing_sale_id = data.get('existing_sale_id')
        
        # For payment on account, we only need customer and total
        if is_payment_on_account:
            if not customer_id:
                return JsonResponse({
                    'success': False,
                    'error': 'Customer is required for payment on account.'
                }, status=400)
            if total <= 0:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid payment amount.'
                }, status=400)
        elif not existing_sale_id and (not items or total <= 0):
            return JsonResponse({
                'success': False,
                'error': 'Invalid cart data.'
            }, status=400)
        
        phone = str(data.get('phone', '')).strip()
        
        import uuid
        import re
        
        # Get customer if specified
        customer = None
        
        if customer_id:
            from apps.customers.models import Customer
            customer = Customer.objects.filter(
                tenant=tenant,
                pk=customer_id
            ).first()
            if customer and customer.phone and not phone:
                phone = str(customer.phone).strip()
                
        # If we have an existing sale, try to get phone from it
        if existing_sale_id and not phone:
            sale_obj = Sale.objects.filter(tenant=tenant, pk=existing_sale_id).first()
            if sale_obj:
                if getattr(sale_obj, 'customer_phone', None):
                    phone = sale_obj.customer_phone
                elif getattr(sale_obj, 'customer', None) and sale_obj.customer.phone:
                    phone = sale_obj.customer.phone

        # Phone Number & Carrier Normalization
        raw_phone = str(phone).strip() if phone else ''
        clean_digits = re.sub(r'\D', '', raw_phone)
        if clean_digits.startswith('233') and len(clean_digits) >= 12:
            phone_local = '0' + clean_digits[3:12]
            phone_intl = '+233' + clean_digits[3:12]
            phone_clean = '233' + clean_digits[3:12]
        elif clean_digits.startswith('0') and len(clean_digits) >= 10:
            phone_local = clean_digits[:10]
            phone_intl = '+233' + clean_digits[1:10]
            phone_clean = '233' + clean_digits[1:10]
        elif len(clean_digits) == 9:
            phone_local = '0' + clean_digits
            phone_intl = '+233' + clean_digits
            phone_clean = '233' + clean_digits
        else:
            phone_local = raw_phone
            phone_intl = raw_phone
            phone_clean = clean_digits

        # Carrier detection (Ghanaian networks)
        network_code = 'MTN'
        paystack_momo_provider = 'mtn'
        if phone_local and len(phone_local) >= 3:
            prefix = phone_local[:3]
            if prefix in ['024', '054', '055', '059', '053']:
                network_code = 'MTN'
                paystack_momo_provider = 'mtn'
            elif prefix in ['020', '050']:
                network_code = 'VODAFONE'
                paystack_momo_provider = 'vod'
            elif prefix in ['027', '057', '026', '056']:
                network_code = 'AIRTELTIGO'
                paystack_momo_provider = 'tgo'

        # Customer name split
        cust_name = customer.name.strip() if customer and customer.name else "Walk-in Customer"
        name_parts = cust_name.split()
        first_name = name_parts[0] if name_parts else "Customer"
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Customer"

        # Generate predictable email based on phone to avoid fraud drops
        if phone_clean:
            customer_email = f'momo_{phone_clean}@hendaxis.com'
        elif customer and customer.email:
            customer_email = customer.email
        else:
            unique_suffix = uuid.uuid4().hex[:8]
            customer_email = f'walkin_{unique_suffix}@hendaxis.com'

        # Generate unique reference
        reference = f"ECASH-{timezone.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"

        sale = None
        if not is_payment_on_account:
            with transaction.atomic():
                if existing_sale_id:
                    sale = Sale.objects.get(
                        tenant=tenant,
                        pk=existing_sale_id,
                        status='PENDING'
                    )
                    sale.paystack_reference = reference
                    sale.save(update_fields=['paystack_reference'])
                else:
                    current_shift = Shift.objects.filter(
                        tenant=tenant,
                        attendant=user,
                        status='OPEN'
                    ).first()
                    
                    sale = Sale.objects.create(
                        tenant=tenant,
                        shop=shop,
                        attendant=user,
                        shift=current_shift,
                        customer=customer,
                        payment_method='ECASH',
                        status='PENDING',
                        discount_amount=discount,
                        paystack_reference=reference
                    )
                    
                    for item_data in items:
                        product = Product.objects.filter(
                            tenant=tenant,
                            pk=item_data['product_id'],
                            is_active=True
                        ).first()
                        
                        if product:
                            SaleItem.objects.create(
                                tenant=tenant,
                                sale=sale,
                                product=product,
                                quantity=Decimal(str(item_data['quantity'])),
                                unit_price=Decimal(str(item_data['unit_price']))
                            )
                    
                    sale.calculate_totals()

        # Checkout type and comprehensive metadata
        checkout_type = provider.get_checkout_type
        authorization_url = ''
        token = ''
        amount_to_charge = total if is_payment_on_account else sale.total

        metadata = {
            'phone': phone_local,
            'phonenumber': phone_local,
            'customer_number': phone_local,
            'intl_phone': phone_intl,
            'clean_phone': phone_clean,
            'network': network_code,
            'paystack_provider': paystack_momo_provider,
            'first_name': first_name,
            'last_name': last_name,
            'device_type': 'pos_terminal',
            'is_pos_checkout': True,
            'sale_id': None if is_payment_on_account else sale.pk,
            'sale_number': None if is_payment_on_account else sale.sale_number,
            'is_payment_on_account': is_payment_on_account,
            'customer_id': customer_id if is_payment_on_account else (customer.pk if customer else None),
            'channels': ['mobile_money', 'card'],
            'custom_fields': [
                {'display_name': 'Phone Number', 'variable_name': 'phone', 'value': phone_local},
                {'display_name': 'Network', 'variable_name': 'network', 'value': network_code},
            ]
        }

        # Initialize for non-inline providers
        if checkout_type != 'inline':
            init_result = provider.initialize_payment(
                amount=amount_to_charge,
                email=customer_email,
                reference=reference,
                metadata=metadata
            )
            if not init_result.success:
                return JsonResponse({
                    'success': False,
                    'error': f'Failed to initialize payment: {init_result.message}'
                }, status=400)
            authorization_url = init_result.authorization_url
            if hasattr(init_result, 'data') and isinstance(init_result.data, dict):
                token = init_result.data.get('token', '')

        return JsonResponse({
            'success': True,
            'sale_id': None if is_payment_on_account else sale.pk,
            'sale_number': None if is_payment_on_account else sale.sale_number,
            'reference': reference,
            'public_key': getattr(provider, 'public_key', ''),
            'checkout_type': checkout_type,
            'authorization_url': authorization_url,
            'token': token,
            'customer_email': customer_email,
            'phone': phone_local,
            'phone_intl': phone_intl,
            'network': network_code,
            'paystack_provider': paystack_momo_provider,
            'tenant_id': tenant.pk,
            'total': str(amount_to_charge),
            'provider': provider.provider_name,
            'is_payment_on_account': is_payment_on_account,
            'customer_id': customer_id if is_payment_on_account else (customer.pk if customer else None),
            'customer_name': customer.name if customer else None
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def verify_ecash_payment(request):
    """
    Verify an e-cash payment and complete the sale or payment on account.
    """
    from apps.payments.models import ECashLedger
    try:
        data = json.loads(request.body)
        user = request.user
        tenant = user.tenant
        
        reference = data.get('reference')
        sale_id = data.get('sale_id')
        is_payment_on_account = data.get('is_payment_on_account', False)
        customer_id = data.get('customer_id')
        amount = Decimal(str(data.get('amount', 0)))
        
        if not reference:
            return JsonResponse({
                'success': False,
                'error': 'Missing payment reference.'
            }, status=400)

        # Idempotency check: if sale is already completed, return success immediately
        if sale_id:
            already_completed = Sale.objects.filter(
                tenant=tenant,
                pk=sale_id,
                paystack_reference=reference,
                status='COMPLETED'
            ).first()
            if already_completed:
                return JsonResponse({
                    'success': True,
                    'sale_id': already_completed.pk,
                    'sale_number': already_completed.sale_number,
                    'message': 'Payment already verified and sale completed.',
                    'already_completed': True
                })
        
        provider_config_id = data.get('provider_config_id')
        token = data.get('token', '')
        
        # Get shop and provider
        shop = user.location
        if not shop or shop.location_type != 'SHOP':
            from apps.core.models import Location
            shop = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True).first()
            
        from apps.payments.services.paystack import get_payment_provider
        provider = get_payment_provider(tenant, shop=shop, config_id=provider_config_id)
        
        if not provider:
            return JsonResponse({
                'success': False,
                'error': 'Payment provider not configured.'
            }, status=400)
        
        # Provider might need token (ExpressPay) or reference (Paystack, AppsnMobile, Nalopay)
        if provider.provider_name == 'ExpressPay':
            result = provider.verify_payment(reference, token=token)
        else:
            result = provider.verify_payment(reference)
        
        if not result.success:
            return JsonResponse({
                'success': False,
                'error': f'Payment verification failed: {result.message}'
            }, status=400)
        
        # Handle payment on account (no sale, just customer payment)
        if is_payment_on_account:
            if not customer_id or amount <= 0:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid payment on account data.'
                }, status=400)
            
            from apps.customers.models import Customer, CustomerTransaction
            customer = Customer.objects.filter(tenant=tenant, pk=customer_id).first()
            
            if not customer:
                return JsonResponse({
                    'success': False,
                    'error': 'Customer not found.'
                }, status=404)
            
            with transaction.atomic():
                # Update customer balance
                balance_before = customer.current_balance
                customer.current_balance -= amount
                customer.save()
                
                # Create transaction record
                txn = CustomerTransaction.objects.create(
                    tenant=tenant,
                    customer=customer,
                    transaction_type='CREDIT',
                    amount=amount,
                    description=f"ECASH Payment (Paystack: {reference[:20]}...)",
                    reference_id=reference,
                    balance_before=balance_before,
                    balance_after=customer.current_balance,
                    performed_by=user
                )
                
                # Record in e-cash ledger
                ECashLedger.record_payment(
                    tenant=tenant,
                    amount=amount,
                    sale=None,
                    paystack_ref=reference,
                    user=user,
                    notes=f"Payment on account for {customer.name} via {provider.provider_name}",
                    provider_config=provider.settings if hasattr(provider, 'settings') else None,
                    metadata=result.data
                )
            
            return JsonResponse({
                'success': True,
                'transaction_id': txn.pk,
                'message': 'Payment verified and recorded.',
                'is_payment_on_account': True
            })
        
        # Regular sale verification
        if not sale_id:
            return JsonResponse({
                'success': False,
                'error': 'Missing sale ID.'
            }, status=400)
        
        # Check if already completed (e.g. from background poll or webhook)
        already_completed = Sale.objects.filter(
            tenant=tenant,
            pk=sale_id,
            paystack_reference=reference,
            status='COMPLETED'
        ).first()
        if already_completed:
            return JsonResponse({
                'success': True,
                'sale_id': already_completed.pk,
                'sale_number': already_completed.sale_number,
                'message': 'Payment already verified and sale completed.',
                'already_completed': True
            })

        # Get the pending sale
        sale = Sale.objects.filter(
            tenant=tenant,
            pk=sale_id,
            paystack_reference=reference,
            status='PENDING'
        ).first()
        
        if not sale:
            return JsonResponse({
                'success': False,
                'error': 'Sale not found or already processed.'
            }, status=404)
        
        # Complete the sale
        with transaction.atomic():
            sale.complete(
                amount_paid=sale.total,
                payment_method='ECASH',
                paystack_ref=reference
            )
            
            # Record in e-cash ledger
            ECashLedger.record_payment(
                tenant=tenant,
                amount=sale.total,
                sale=sale,
                paystack_ref=reference,
                user=user,
                notes=f"Sale payment via {provider.provider_name}",
                provider_config=provider.settings if hasattr(provider, 'settings') else None,
                metadata=result.data
            )

            # Notify Shop Manager if using strict sales workflow
            if tenant.use_strict_sales_workflow:
                from apps.notifications.models import Notification
                from apps.core.models import User
                shop_managers = User.objects.filter(
                    tenant=tenant,
                    location=sale.shop,
                    role__name='SHOP_MANAGER',
                    is_active=True
                )
                for manager in shop_managers:
                    Notification.objects.create(
                        tenant=tenant,
                        user=manager,
                        title='Invoice Paid (E-Cash)',
                        message=f'Invoice {sale.sale_number} has been paid via E-Cash and is ready for dispatch.',
                        notification_type='INVOICE_PAID',
                        reference_type='Sale',
                        reference_id=sale.id
                    )
        
        return JsonResponse({
            'success': True,
            'sale_id': sale.pk,
            'sale_number': sale.sale_number,
            'message': 'Payment verified and sale completed.'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def api_pending_ecash_sales(request):
    """Return a list of pending e-cash sales for the current shift to allow manual verification."""
    user = request.user
    tenant = user.tenant
    
    # Get current shift if any
    current_shift = Shift.objects.filter(
        tenant=tenant,
        attendant=user,
        status='OPEN'
    ).first()
    
    if not current_shift:
        return JsonResponse({'success': True, 'sales': []})
        
    pending_sales = Sale.objects.filter(
        tenant=tenant,
        shift=current_shift,
        payment_method='ECASH',
        status='PENDING'
    ).order_by('-created_at')
    
    sales_data = []
    for s in pending_sales:
        sales_data.append({
            'id': s.id,
            'sale_number': s.sale_number,
            'reference': s.paystack_reference,
            'total': str(s.total),
            'customer_name': s.customer.name if s.customer else 'Walk-in',
            'created_at': s.created_at.strftime('%I:%M %p')
        })
        
    return JsonResponse({'success': True, 'sales': sales_data})


# ============ Offline Sync API ============

def _process_offline_sale_payload(data, user, tenant):
    """
    Core processor for a single offline sale payload.
    Used by both single sync and batch sync APIs.
    """
    client_sale_id = data.get('client_sale_id')
    if not client_sale_id:
        return {
            'success': False,
            'client_sale_id': None,
            'status': 'failed',
            'error': 'Missing client_sale_id for offline sync.'
        }

    # Idempotency check - if this sale was already synced, return existing record
    existing = Sale.objects.filter(
        tenant=tenant,
        client_sale_id=client_sale_id
    ).first()
    if existing:
        return {
            'success': True,
            'client_sale_id': client_sale_id,
            'sale_id': existing.pk,
            'sale_number': existing.sale_number,
            'status': 'already_synced',
            'already_synced': True,
            'has_conflicts': getattr(existing, 'has_sync_conflict', False),
            'conflicts': [existing.sync_conflict_notes] if existing.has_sync_conflict else [],
            'message': 'Sale already synced (duplicate client_sale_id).'
        }

    # Extract sale data
    cart_items = data.get('items', [])
    discount_amount = Decimal(str(data.get('discount_amount', 0)))
    customer_id = data.get('customer_id')
    payment_method = data.get('payment_method', 'CASH')
    amount_paid = Decimal(str(data.get('amount_paid', 0)))
    offline_created_at = data.get('offline_created_at')
    is_payment_on_account = data.get('is_payment_on_account', False)

    # Handle payment on account
    if is_payment_on_account:
        if not customer_id:
            return {'success': False, 'client_sale_id': client_sale_id, 'status': 'failed', 'error': 'Customer required for payment on account'}
        if amount_paid <= 0:
            return {'success': False, 'client_sale_id': client_sale_id, 'status': 'failed', 'error': 'Payment amount must be positive'}
        
        from apps.customers.models import Customer, CustomerTransaction
        customer = Customer.objects.filter(tenant=tenant, pk=customer_id).first()
        if not customer:
            return {'success': False, 'client_sale_id': client_sale_id, 'status': 'failed', 'error': 'Customer not found'}
            
        try:
            with transaction.atomic():
                balance_before = customer.current_balance
                customer.current_balance -= amount_paid
                customer.save(update_fields=['current_balance'])
                
                txn = CustomerTransaction.objects.create(
                    tenant=tenant,
                    customer=customer,
                    transaction_type='CREDIT',
                    amount=amount_paid,
                    description=f"Payment on account ({payment_method})",
                    reference_id=f"POA-OFF-{timezone.now().strftime('%Y%m%d%H%M%S')}",
                    balance_before=balance_before,
                    balance_after=customer.current_balance,
                    performed_by=user
                )
                
                return {
                    'success': True,
                    'client_sale_id': client_sale_id,
                    'sale_id': txn.pk,
                    'sale_number': 'POA-' + str(txn.pk),
                    'status': 'synced',
                    'has_conflicts': False,
                    'conflicts': [],
                    'message': 'Offline payment synced successfully.'
                }
        except Exception as e:
            return {'success': False, 'client_sale_id': client_sale_id, 'status': 'failed', 'error': str(e)}

    if not cart_items:
        return {'success': False, 'client_sale_id': client_sale_id, 'status': 'failed', 'error': 'No items in offline sale.'}

    # Only CASH, CREDIT, MIXED and MOMO allowed for offline sales
    if payment_method not in ('CASH', 'CREDIT', 'MIXED', 'MOMO'):
        return {'success': False, 'client_sale_id': client_sale_id, 'status': 'failed', 'error': 'Only Cash, Credit, Mixed or Momo payments are supported offline.'}

    # Get shop
    shop = user.location
    if not shop or shop.location_type != 'SHOP':
        shop = Location.objects.filter(
            tenant=tenant,
            location_type='SHOP',
            is_active=True
        ).first()

    if not shop:
        return {'success': False, 'client_sale_id': client_sale_id, 'status': 'failed', 'error': 'No shop location configured.'}

    customer = None
    if customer_id:
        from apps.customers.models import Customer
        customer = Customer.objects.filter(tenant=tenant, pk=customer_id).first()

    current_shift = Shift.objects.filter(
        tenant=tenant,
        attendant=user,
        status='OPEN'
    ).first()

    sync_conflicts = []

    with transaction.atomic():
        sale = Sale(
            tenant=tenant,
            shop=shop,
            attendant=user,
            shift=current_shift,
            customer=customer,
            payment_method=payment_method,
            status='PENDING',
            discount_amount=discount_amount,
            client_sale_id=client_sale_id,
            synced_at=timezone.now(),
        )

        if offline_created_at:
            from datetime import datetime
            try:
                sale.offline_created_at = datetime.fromisoformat(
                    offline_created_at.replace('Z', '+00:00')
                )
            except (ValueError, AttributeError):
                pass

        sale.save()

        # Create sale items and check stock conflicts
        from apps.inventory.models import InventoryLedger, Batch
        from django.db.models import Sum

        for item_data in cart_items:
            product = Product.objects.filter(
                tenant=tenant,
                pk=item_data.get('product_id'),
                is_active=True
            ).first()

            if not product:
                sync_conflicts.append(
                    f"Product ID {item_data.get('product_id')} ({item_data.get('product_name', 'Unknown')}) not found or inactive."
                )
                continue

            quantity = Decimal(str(item_data.get('quantity', 0)))
            available = InventoryLedger.objects.filter(
                tenant=tenant,
                product=product,
                location=shop
            ).aggregate(total=Sum('quantity'))['total'] or Decimal('0')

            if available < quantity:
                sync_conflicts.append(
                    f"{product.name}: sold {quantity} offline, available in ledger was {available}"
                )

            batch = None
            batch_id = item_data.get('batch_id')
            if batch_id:
                batch = Batch.objects.filter(tenant=tenant, pk=batch_id).first()

            SaleItem.objects.create(
                tenant=tenant,
                sale=sale,
                product=product,
                batch=batch,
                quantity=quantity,
                unit_price=Decimal(str(item_data.get('unit_price', 0))),
            )

        sale.calculate_totals()

        if payment_method == 'MOMO':
            shop_settings = getattr(shop, 'shop_settings', None)
            if not shop_settings and shop:
                try:
                    from apps.sales.models import ShopSettings
                    shop_settings = ShopSettings.objects.get(shop=shop)
                except ShopSettings.DoesNotExist:
                    shop_settings = None
            if shop_settings and shop_settings.disallow_manual_momo_amount:
                amount_paid = sale.total

        if sync_conflicts:
            sale.has_sync_conflict = True
            sale.sync_conflict_notes = '\n'.join(sync_conflicts)
            sale.save(update_fields=['has_sync_conflict', 'sync_conflict_notes'])

        try:
            sale.complete(amount_paid, payment_method)
        except ValidationError as ve:
            sale.has_sync_conflict = True
            sale.sync_conflict_notes = (sale.sync_conflict_notes or '') + f'\nCompletion error: {str(ve)}'
            sale.save(update_fields=['has_sync_conflict', 'sync_conflict_notes'])
            sync_conflicts.append(f'Completion error: {str(ve)}')

    return {
        'success': True,
        'client_sale_id': client_sale_id,
        'sale_id': sale.pk,
        'sale_number': sale.sale_number,
        'status': 'conflict' if sync_conflicts else 'synced',
        'has_conflicts': bool(sync_conflicts),
        'conflicts': sync_conflicts,
        'message': 'Offline sale synced successfully.' if not sync_conflicts else 'Sale synced with inventory conflicts - please review.'
    }


@login_required
@require_POST
def api_sync_offline_sales(request):
    """
    Sync a single offline sale to the server.
    Uses client_sale_id for idempotency (backward-compatible).
    """
    try:
        data = json.loads(request.body)
        result = _process_offline_sale_payload(data, request.user, request.user.tenant)
        status_code = 200 if result.get('success') else 400
        return JsonResponse(result, status=status_code)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data.'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def api_sync_offline_batch(request):
    """
    Sync a batch of offline sales to the server in chronological order.
    Executes each sale in an atomic sub-transaction so partial success is preserved.
    """
    try:
        data = json.loads(request.body)
        sales_list = data.get('sales', [])
        if not isinstance(sales_list, list):
            return JsonResponse({'success': False, 'error': 'Invalid payload: "sales" must be a list.'}, status=400)

        if not sales_list:
            return JsonResponse({
                'success': True,
                'total': 0,
                'synced_count': 0,
                'conflict_count': 0,
                'failed_count': 0,
                'results': []
            })

        user = request.user
        tenant = user.tenant

        # Sort sales chronologically by offline_created_at
        def get_offline_timestamp(s):
            val = s.get('offline_created_at') or ''
            return str(val)

        sorted_sales = sorted(sales_list, key=get_offline_timestamp)

        results = []
        synced_count = 0
        conflict_count = 0
        failed_count = 0

        for sale_data in sorted_sales:
            try:
                res = _process_offline_sale_payload(sale_data, user, tenant)
                results.append(res)
                if res.get('success'):
                    if res.get('has_conflicts'):
                        conflict_count += 1
                    else:
                        synced_count += 1
                else:
                    failed_count += 1
            except Exception as item_err:
                failed_count += 1
                results.append({
                    'success': False,
                    'client_sale_id': sale_data.get('client_sale_id'),
                    'status': 'failed',
                    'error': str(item_err)
                })

        return JsonResponse({
            'success': True,
            'total': len(sorted_sales),
            'synced_count': synced_count,
            'conflict_count': conflict_count,
            'failed_count': failed_count,
            'results': results
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data.'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============ Excel Export Views ============

class SaleListExportView(LoginRequiredMixin, View):
    """Export sales list to Excel."""

    def get(self, request):
        from datetime import datetime
        from django.db.models import Q
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        user = request.user
        role_name = user.role.name if user.role else None

        queryset = Sale.objects.filter(
            tenant=user.tenant
        ).select_related('shop', 'attendant', 'customer').order_by('-created_at')

        # Role-based filtering (same as SaleListView)
        if role_name not in ['AUDITOR', 'ACCOUNTANT', 'ADMIN']:
            if user.location and user.location.location_type == 'SHOP':
                queryset = queryset.filter(shop=user.location)
        else:
            shop_id = request.GET.get('shop')
            if shop_id:
                queryset = queryset.filter(shop_id=shop_id)
            attendant_id = request.GET.get('attendant')
            if attendant_id:
                queryset = queryset.filter(attendant_id=attendant_id)

        # Date range filter
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')
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

        payment = request.GET.get('payment')
        if payment:
            queryset = queryset.filter(payment_method=payment)

        headers = ['Sale #', 'Date', 'Shop', 'Attendant', 'Customer', 'Payment Method',
                    'Status', 'Subtotal', 'Discount', 'Total', 'Amount Paid']
        rows = []
        for sale in queryset:
            rows.append([
                sale.sale_number,
                sale.created_at.strftime('%Y-%m-%d %H:%M') if sale.created_at else '',
                sale.shop.name if sale.shop else '',
                sale.attendant.get_full_name() or sale.attendant.email if sale.attendant else '',
                sale.customer.name if sale.customer else '',
                sale.get_payment_method_display(),
                sale.get_status_display(),
                float(sale.subtotal),
                float(sale.discount_amount),
                float(sale.total),
                float(sale.amount_paid),
            ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            date_range_str = "All Time"
            if date_from and date_to:
                date_range_str = f"{date_from} to {date_to}"
            elif date_from:
                date_range_str = f"From {date_from}"
            elif date_to:
                date_range_str = f"Until {date_to}"
                
            shop_name = "All Shops"
            if role_name not in ['AUDITOR', 'ACCOUNTANT', 'ADMIN']:
                if user.location and user.location.location_type == 'SHOP':
                    shop_name = user.location.name
            else:
                shop_id = request.GET.get('shop')
                if shop_id:
                    from apps.core.models import Location
                    loc = Location.objects.filter(pk=shop_id).first()
                    if loc: shop_name = loc.name
            
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop_name,
                'date_range': date_range_str
            }
            return export_to_pdf('sales_export.pdf', 'Sales History', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Sales', headers, rows)
            return build_excel_response(wb, 'sales_export.xlsx')


class ShopSalesReportExportView(LoginRequiredMixin, View):
    """Export shop sales report to Excel (attendant + product breakdown)."""

    def get(self, request):
        from django.db.models import Sum, Count, Q
        from datetime import timedelta, datetime
        from apps.core.excel_utils import create_export_workbook, add_sheet, build_excel_response

        user = request.user
        role_name = user.role.name if user.role else None

        if role_name not in ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            messages.error(request, 'You do not have permission to export this report.')
            return redirect('core:dashboard')

        # Get the shop
        shop = user.location
        if not shop and role_name in ['ADMIN', 'ACCOUNTANT', 'AUDITOR']:
            shop_id = request.GET.get('shop')
            if shop_id:
                shop = Location.objects.filter(
                    tenant=user.tenant, pk=shop_id, location_type='SHOP'
                ).first()

        if not shop or shop.location_type != 'SHOP':
            messages.error(request, 'No shop selected for export.')
            return redirect('sales:shop_sales_report')

        # Date range
        today = timezone.now().date()
        date_from_str = request.GET.get('date_from')
        date_to_str = request.GET.get('date_to')
        date_range = request.GET.get('range', 'month')

        if date_from_str and date_to_str:
            try:
                date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
                date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
            except ValueError:
                date_from = today - timedelta(days=30)
                date_to = today
        elif date_range == 'today':
            date_from = today
            date_to = today
        elif date_range == 'week':
            date_from = today - timedelta(days=7)
            date_to = today
        else:
            date_from = today - timedelta(days=30)
            date_to = today

        attendant_id = request.GET.get('attendant')
        payment_filter = request.GET.get('payment')

        # Build sales filter
        sales_filter = Q(
            tenant=user.tenant, shop=shop, status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'],
            created_at__date__gte=date_from, created_at__date__lte=date_to
        )
        if attendant_id:
            sales_filter &= Q(attendant_id=attendant_id)
        if payment_filter:
            sales_filter &= Q(payment_method=payment_filter)

        # Sheet 1: Attendant Breakdown
        attendant_stats = Sale.objects.filter(sales_filter).values(
            'attendant__first_name', 'attendant__last_name', 'attendant__email'
        ).annotate(
            total_sales=Count('id'),
            total_revenue=Sum('total'),
            cash_amount=Sum('total', filter=Q(payment_method='CASH')),
            ecash_amount=Sum('total', filter=Q(payment_method='ECASH')),
        ).order_by('-total_revenue')

        att_headers = ['Attendant', 'Sales Count', 'Revenue', 'Cash', 'E-Cash']
        att_rows = []
        for a in attendant_stats:
            name = f"{a['attendant__first_name'] or ''} {a['attendant__last_name'] or ''}".strip() or a['attendant__email']
            att_rows.append([
                name,
                a['total_sales'],
                float(a['total_revenue'] or 0),
                float(a['cash_amount'] or 0),
                float(a['ecash_amount'] or 0),
            ])

        items_filter = {
            'sale__tenant': user.tenant,
            'sale__shop': shop,
            'sale__status__in': ['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED'],
            'sale__created_at__date__gte': date_from,
            'sale__created_at__date__lte': date_to,
        }
        if attendant_id:
            items_filter['sale__attendant_id'] = attendant_id
        if payment_filter:
            items_filter['sale__payment_method'] = payment_filter

        all_products = SaleItem.objects.filter(
            **items_filter
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
            date_range_str = f"{date_from} to {date_to}" if date_from != date_to else str(date_from)
            metadata = {
                'generator_name': user.get_full_name() or user.email,
                'shop_name': shop.name,
                'date_range': date_range_str
            }
            return export_to_pdf(
                f'shop_sales_report_{shop.name}_{date_from}_to_{date_to}.pdf', 
                'Shop Sales Report', 
                att_headers, att_rows,
                additional_tables=[('Product Breakdown', prod_headers, prod_rows)],
                metadata=metadata
            )
        else:
            wb = create_export_workbook('Attendant Breakdown', att_headers, att_rows)
            add_sheet(wb, 'Product Breakdown', prod_headers, prod_rows)
            return build_excel_response(wb, f'shop_sales_report_{shop.name}_{date_from}_to_{date_to}.xlsx')


class ShopSettingsUpdateView(LoginRequiredMixin, AdminOrManagerRequiredMixin, UpdateView):
    """
    Shop Manager / Admin view to configure print layouts, receipts, and POS stock behavior.
    Supports multi-shop selection for admins and managers.
    """
    model = ShopSettings
    form_class = ShopManagerSettingsForm
    template_name = 'sales/shop_settings.html'

    def get_object(self, queryset=None):
        from apps.core.models import Location
        user = self.request.user
        role_name = user.role.name if user.role else ''
        can_select_any_shop = bool(user.is_superuser or role_name in ['ADMIN', 'ACCOUNTANT', 'AUDITOR'])

        shop = None
        shop_param = self.request.POST.get('shop') or self.request.GET.get('shop')

        # If a shop param is provided, validate it for the tenant
        if shop_param and (can_select_any_shop or (user.location and str(user.location_id) == str(shop_param))):
            shop = Location.objects.filter(
                tenant=user.tenant,
                location_type='SHOP',
                pk=shop_param,
                is_active=True
            ).first()

        # If not provided, fallback to user's assigned shop location
        if not shop and user.location and user.location.location_type == 'SHOP':
            shop = user.location

        # Fallback to first active shop for tenant
        if not shop:
            shop = Location.objects.filter(
                tenant=user.tenant,
                location_type='SHOP',
                is_active=True
            ).order_by('name').first()

        if not shop:
            from django.http import Http404
            raise Http404("No shop found for this tenant.")

        settings, _ = ShopSettings.objects.get_or_create(
            tenant=user.tenant,
            shop=shop,
            defaults={'receipt_printer_type': 'THERMAL_80MM'}
        )
        return settings

    def get_success_url(self):
        return f"{reverse_lazy('sales:shop_settings')}?shop={self.object.shop.pk}"

    def get_context_data(self, **kwargs):
        from apps.core.models import Location
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role_name = user.role.name if user.role else ''
        can_switch_shops = bool(user.is_superuser or role_name in ['ADMIN', 'ACCOUNTANT', 'AUDITOR'])

        available_shops = Location.objects.filter(
            tenant=user.tenant,
            location_type='SHOP',
            is_active=True
        ).order_by('name')

        context['current_shop'] = self.object.shop
        context['available_shops'] = available_shops
        context['can_switch_shops'] = can_switch_shops and (available_shops.count() > 1)
        return context

    def form_valid(self, form):
        from apps.core.models import Location
        response = super().form_valid(form)
        current_settings = self.object

        if self.request.POST.get('apply_to_all_shops') == '1':
            other_shops = Location.objects.filter(
                tenant=self.request.user.tenant,
                location_type='SHOP',
                is_active=True
            ).exclude(pk=current_settings.shop.pk)

            updated_count = 1
            for other_shop in other_shops:
                s, _ = ShopSettings.objects.get_or_create(
                    tenant=self.request.user.tenant,
                    shop=other_shop,
                    defaults={'receipt_printer_type': current_settings.receipt_printer_type}
                )
                s.show_items_with_physical_stock_in_pos = current_settings.show_items_with_physical_stock_in_pos
                s.hide_zero_stock_in_pos = current_settings.hide_zero_stock_in_pos
                s.auto_print_receipts = current_settings.auto_print_receipts
                s.receipt_printer_type = current_settings.receipt_printer_type
                s.receipt_font_family = current_settings.receipt_font_family
                s.receipt_font_weight = current_settings.receipt_font_weight
                s.receipt_font_size = current_settings.receipt_font_size
                s.show_logo_on_receipt = current_settings.show_logo_on_receipt
                s.show_customer_info_on_receipt = current_settings.show_customer_info_on_receipt
                s.receipt_header = current_settings.receipt_header
                s.receipt_footer = current_settings.receipt_footer
                s.enable_momo_payment = current_settings.enable_momo_payment
                s.disallow_manual_momo_amount = current_settings.disallow_manual_momo_amount
                s.save()
                updated_count += 1
            messages.success(self.request, f"Settings saved for {current_settings.shop.name} and synced to all {updated_count} shops.")
        else:
            messages.success(self.request, f"Settings for {current_settings.shop.name} have been updated.")

        return response


class AdminShopPaymentConfigView(LoginRequiredMixin, AdminRequiredMixin, UpdateView):
    """
    Admin view to inject API Keys securely for specific shops to override 
    the Global Tenant API keys for E-Cash.
    """
    model = ShopSettings
    form_class = AdminShopPaymentSettingsForm
    template_name = 'sales/admin_shop_payment_settings.html'
    
    def get_object(self, queryset=None):
        shop_id = self.kwargs.get('shop_id')
        shop = get_object_or_404(Location, id=shop_id, tenant=self.request.user.tenant, location_type='SHOP')
        self.shop_name = shop.name
        settings, _ = ShopSettings.objects.get_or_create(
            tenant=self.request.user.tenant,
            shop=shop,
            defaults={'receipt_printer_type': 'THERMAL_80MM'}
        )
        return settings

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['shop_name'] = self.shop_name
        return context

    def get_success_url(self):
        return reverse_lazy('core:location_list')

    def form_valid(self, form):
        messages.success(self.request, f"Payment settings for {self.shop_name} updated.")
        return super().form_valid(form)


class CustomerFootprintView(LoginRequiredMixin, View):
    """
    Displays a unified list of all customers (registered + walk-in) who have
    transacted through the system, with their transaction counts, total value,
    and last visit date.

    Accessible to: SHOP_MANAGER (own shop), ACCOUNTANT, AUDITOR, ADMIN (all shops).
    Supports filtering by date range, shop, and customer type.
    Supports sorting by name, transaction count, or total value.
    """

    ALLOWED_ROLES = ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN', 'SHOP_CASHIER']

    def get(self, request):
        from django.db.models import Count, Sum, Max
        from apps.core.models import Location

        user = request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None

        if role_name not in self.ALLOWED_ROLES:
            messages.error(request, 'You do not have permission to view customer footprints.')
            return redirect('sales:sale_list')

        # --- Filter parameters ---
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        shop_id = request.GET.get('shop', '')
        customer_type = request.GET.get('customer_type', '')  # 'registered', 'walk_in', ''
        sort_by = request.GET.get('sort', 'name')             # 'name', 'count', 'value', 'last_visit'
        sort_dir = request.GET.get('dir', 'asc')
        search_query = request.GET.get('search', '').lower().strip()

        # Base queryset — only paid sales
        qs = Sale.objects.filter(
            tenant=tenant,
            status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']
        )

        # Restrict shop managers and cashiers to their own shop
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            qs = qs.filter(shop=user.location)
        elif shop_id:
            qs = qs.filter(shop_id=shop_id)

        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        # --- Build registered customer rows ---
        registered_rows = []
        if customer_type in ('', 'registered'):
            reg_qs = (
                qs.filter(customer__isnull=False)
                .values('customer__id', 'customer__name', 'customer__phone')
                .annotate(
                    tx_count=Count('id'),
                    total_value=Sum('total'),
                    last_visit=Max('created_at'),
                )
                .order_by()
            )
            for row in reg_qs:
                registered_rows.append({
                    'type': 'registered',
                    'customer_id': row['customer__id'],
                    'name': row['customer__name'] or '(No Name)',
                    'phone': row['customer__phone'] or '-',
                    'tx_count': row['tx_count'],
                    'total_value': row['total_value'] or 0,
                    'last_visit': row['last_visit'],
                })

        # --- Build walk-in customer rows (group by phone, then name) ---
        walk_in_rows = []
        if customer_type in ('', 'walk_in'):
            # Walk-ins: customer field is NULL but customer_name or customer_phone is set
            walkin_qs = (
                qs.filter(customer__isnull=True)
                .exclude(customer_name='', customer_phone='')
            )

            # Group by phone (primary) then name
            walkin_agg = {}
            for sale in walkin_qs.values('customer_name', 'customer_phone', 'total', 'created_at'):
                phone = sale['customer_phone'].strip() if sale['customer_phone'] else ''
                name = sale['customer_name'].strip() if sale['customer_name'] else 'Walk-in Customer'
                # Key: phone if provided, else name
                key = phone if phone else name
                if key not in walkin_agg:
                    walkin_agg[key] = {
                        'type': 'walk_in',
                        'customer_id': None,
                        'name': name,
                        'phone': phone or '-',
                        'tx_count': 0,
                        'total_value': 0,
                        'last_visit': None,
                    }
                walkin_agg[key]['tx_count'] += 1
                walkin_agg[key]['total_value'] += sale['total'] or 0
                if sale['created_at']:
                    if not walkin_agg[key]['last_visit'] or sale['created_at'] > walkin_agg[key]['last_visit']:
                        walkin_agg[key]['last_visit'] = sale['created_at']
                # Prefer phone as name display if multiple names map to same phone
                if phone and walkin_agg[key]['name'] == 'Walk-in Customer':
                    walkin_agg[key]['name'] = name

            walk_in_rows = list(walkin_agg.values())

        all_rows = registered_rows + walk_in_rows

        if search_query:
            all_rows = [
                r for r in all_rows 
                if search_query in (r['name'] or '').lower() or search_query in (r['phone'] or '').lower()
            ]

        # --- Sorting ---
        reverse = (sort_dir == 'desc')
        if sort_by == 'count':
            all_rows.sort(key=lambda x: x['tx_count'], reverse=reverse)
        elif sort_by == 'value':
            all_rows.sort(key=lambda x: float(x['total_value']), reverse=reverse)
        elif sort_by == 'last_visit':
            all_rows.sort(key=lambda x: x['last_visit'] or '', reverse=reverse)
        else:
            all_rows.sort(key=lambda x: (x['name'] or '').lower(), reverse=reverse)

        # Pagination
        from django.core.paginator import Paginator
        paginator = Paginator(all_rows, 50)
        page_obj = paginator.get_page(request.GET.get('page'))

        # Shops for filter dropdown
        if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            shops = Location.objects.filter(id=user.location_id)
        else:
            shops = Location.objects.filter(tenant=tenant, location_type='SHOP')

        context = {
            'page_obj': page_obj,
            'date_from': date_from,
            'date_to': date_to,
            'shop_id': shop_id,
            'customer_type': customer_type,
            'sort_by': sort_by,
            'sort_dir': sort_dir,
            'shops': shops,
            'total_rows': len(all_rows),
            'total_value': sum(float(r['total_value']) for r in all_rows),
            'search_query': search_query,
        }
        return render(request, 'sales/customer_footprint.html', context)


from django.http import JsonResponse
from django.db.models import Sum, Q

class FootprintAutoSuggestAPIView(LoginRequiredMixin, View):
    def get(self, request):
        user = request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None
        
        if role_name not in CustomerFootprintView.ALLOWED_ROLES:
            return JsonResponse({'error': 'Unauthorized'}, status=403)
            
        search_query = request.GET.get('q', '').strip()
        if not search_query:
            return JsonResponse([], safe=False)

        from apps.customers.models import Customer
        from .models import Sale
        
        customers = Customer.objects.filter(tenant=tenant)
        if role_name == 'SHOP_MANAGER':
            customers = customers.filter(shop=user.location)
            
        customers = customers.filter(
            Q(name__icontains=search_query) | Q(phone__icontains=search_query)
        )[:10]
        
        results = set()
        for c in customers:
            results.add(f"{c.name} - {c.phone}" if c.phone else c.name)
            
        sales = Sale.objects.filter(tenant=tenant, customer__isnull=True)
        if role_name == 'SHOP_MANAGER':
            sales = sales.filter(shop=user.location)
            
        sales = sales.filter(
            Q(customer_name__icontains=search_query) | Q(customer_phone__icontains=search_query)
        ).values('customer_name', 'customer_phone').distinct()[:10]
        
        for s in sales:
            name = s.get('customer_name') or 'Walk-in Customer'
            phone = s.get('customer_phone') or ''
            results.add(f"{name} - {phone}" if phone else name)
            
        return JsonResponse([{'text': r} for r in list(results)[:10]], safe=False)

class FootprintItemAggregationAPIView(LoginRequiredMixin, View):
    def get(self, request):
        user = request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None
        
        if role_name not in CustomerFootprintView.ALLOWED_ROLES:
            return JsonResponse({'error': 'Unauthorized'}, status=403)
            
        customer_id = request.GET.get('customer_id')
        name = request.GET.get('name')
        phone = request.GET.get('phone')
        
        from .models import SaleItem, Sale
        from django.db.models import F, Sum
        
        sales = Sale.objects.filter(
            tenant=tenant, 
            status__in=['COMPLETED', 'PENDING_DISPATCH', 'PARTIALLY_DISPATCHED']
        )
        if role_name == 'SHOP_MANAGER':
            sales = sales.filter(shop=user.location)
            
        if customer_id and customer_id != 'None':
            sales = sales.filter(customer_id=customer_id)
        elif phone and phone != '-':
            sales = sales.filter(customer_phone=phone, customer__isnull=True)
        elif name:
            sales = sales.filter(customer_name=name, customer__isnull=True)
        else:
            return JsonResponse({'items': []})
            
        items = SaleItem.objects.filter(sale__in=sales).values('product__name').annotate(
            total_qty=Sum('quantity'),
            total_value=Sum(F('quantity') * F('unit_price'))
        ).order_by('-total_value')
        
        result = []
        for item in items:
            result.append({
                'product_name': item['product__name'],
                'total_qty': float(item['total_qty'] or 0),
                'total_value': float(item['total_value'] or 0)
            })
            
        return JsonResponse({'items': result})
