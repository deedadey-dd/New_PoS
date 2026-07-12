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
from django.db import transaction
from django.utils import timezone

from .models import Sale, SaleItem, Shift, ShopSettings
from apps.inventory.models import Product, ShopPrice
from apps.core.models import Location
from apps.core.mixins import PaginationMixin, SortableMixin
from apps.core.decorators import AdminOrManagerRequiredMixin, AdminRequiredMixin
from .forms import ShopManagerSettingsForm, AdminShopPaymentSettingsForm


class POSView(LoginRequiredMixin, View):
    """Main POS interface."""
    template_name = 'sales/pos.html'
    
    def get(self, request):
        from django.db.models import Sum, Prefetch
        from apps.inventory.models import InventoryLedger, Category
        from apps.customers.models import Customer
        
        # Get user's shop location
        user_shop = request.user.location
        
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
        
        context = {
            'shop': user_shop,
            'products': json.dumps(products_with_prices),
            'categories': categories,
            'customers': json.dumps(list(customers), default=str),
            'available_providers': json.dumps(available_providers),
            'shop_settings': shop_settings,
            'shift': open_shift,
            'allow_negative_stock': request.user.tenant.allow_negative_stock,
            'currency_symbol': request.user.tenant.currency_symbol if request.user.tenant.currency else '$',
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
        
        # Calculate sales breakdown
        from django.db.models import Sum, Q
        sales_qs = shift.sales.filter(status='COMPLETED')
        
        cash_sales = sales_qs.filter(payment_method='CASH').aggregate(
            total=Sum('total'))['total'] or Decimal('0')
        ecash_sales = sales_qs.filter(payment_method='ECASH').aggregate(
            total=Sum('total'))['total'] or Decimal('0')
        momo_sales = sales_qs.filter(payment_method='MOMO').aggregate(
            total=Sum('total'))['total'] or Decimal('0')
        credit_sales = sales_qs.filter(payment_method='CREDIT').aggregate(
            total=Sum('total'))['total'] or Decimal('0')
        mixed_sales = sales_qs.filter(payment_method='MIXED').aggregate(
            total=Sum('amount_paid'))['total'] or Decimal('0')  # Only cash portion
        
        all_sales = sales_qs.aggregate(total=Sum('total'))['total'] or Decimal('0')
        payments_on_account = shift.payments_on_account
        total_cash = shift.opening_cash + cash_sales + mixed_sales + payments_on_account
        
        return render(request, self.template_name, {
            'shift': shift,
            'expected_cash': total_cash,  # Opening + Cash Sales portion + Payments
            'total_sales': all_sales,  # All sales
            'cash_sales': cash_sales + mixed_sales,  # Cash portion only
            'ecash_sales': ecash_sales,
            'momo_sales': momo_sales,
            'credit_sales': credit_sales,
            'payments_on_account': payments_on_account,
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
        
        if role_name == 'ATTENDANT':
            qs = qs.filter(attendant=self.request.user)
        elif role_name == 'SHOP_MANAGER':
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
        sales_qs = shift.sales.filter(status='COMPLETED')
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
    sortable_fields = ['created_at', 'sale_number', 'total', 'amount_paid', 'status', 'payment_method', 'attendant__username']
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
        
        queryset = Sale.objects.filter(
            tenant=user.tenant
        ).select_related('shop', 'attendant').order_by('-created_at')
        
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
            
        # Dispatch status filter
        dispatch_status = self.request.GET.get('dispatch_status')
        if dispatch_status == 'pending':
            from django.db.models import Q
            queryset = queryset.filter(
                Q(status='PENDING_DISPATCH') | Q(status='COMPLETED', is_dispatched=False)
            )
        elif dispatch_status in ['dispatched', 'completed']:
            queryset = queryset.filter(status='COMPLETED', is_dispatched=True)
            
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
        
        # Preserve filter values
        context['per_page'] = self.get_paginate_by(None)
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['selected_shop'] = self.request.GET.get('shop', '')
        context['selected_attendant'] = self.request.GET.get('attendant', '')
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_payment'] = self.request.GET.get('payment', '')
        context['selected_dispatch_status'] = self.request.GET.get('dispatch_status', '')
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
        
        return context


class DispatchListView(SaleListView):
    """List of all fully or partially dispatched sales."""
    template_name = 'sales/dispatch_list.html'

    def get_queryset(self):
        from django.db.models import Q
        queryset = super().get_queryset()
        return queryset.filter(
            Q(is_dispatched=True) | Q(items__dispatched_quantity__gt=0)
        ).distinct()


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
    
    if str(pk_or_number).startswith('S'):
        sale = get_object_or_404(queryset, sale_number=pk_or_number, tenant=request.user.tenant)
    else:
        sale = get_object_or_404(queryset, pk=pk_or_number, tenant=request.user.tenant)
        
    items = []
    for item in sale.items.all():
        items.append({
            'id': item.id,
            'product_name': item.product.name,
            'sku': item.product.sku,
            'quantity': str(item.quantity),
            'dispatched_quantity': str(item.dispatched_quantity),
            'remaining_quantity': str(item.remaining_quantity),
            'is_fully_dispatched': item.is_fully_dispatched,
            'unit_price': str(item.unit_price),
            'total': str(item.total),
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

    data = {
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
        'change_given': str(sale.change_given) if sale.change_given else None,
        'overpayment_credited': overpayment_credited,
        'customer': sale.customer.name if sale.customer else (sale.customer_name or None),
        'customer_id': sale.customer.pk if sale.customer else None,
        'has_registered_customer': bool(sale.customer),
        'customer_name': sale.customer_name,
        'customer_phone': sale.customer_phone,
        'is_dispatched': sale.is_dispatched,
        'all_items_dispatched': sale.all_items_dispatched,
        'allow_partial_dispatch': sale.shop.allow_partial_dispatch if sale.shop else True,
        'items': items,
        'dispatch_history': dispatch_history,
    }
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
    if role_name not in ['SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN', 'SHOP_CASHIER']:
        return JsonResponse({'success': False, 'error': 'You do not have permission to request or process refunds.'}, status=403)

    sale = get_object_or_404(Sale, pk=pk, tenant=tenant)

    # Shop managers and cashiers can only refund their own shop
    if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and sale.shop != request.user.location:
        return JsonResponse({'success': False, 'error': 'You can only request refunds for your own shop.'}, status=403)

    if sale.status == 'REFUNDED':
        return JsonResponse({'success': False, 'error': 'Sale is already refunded.'}, status=400)

    # Check for an already-pending refund request
    from .models import RefundRequest
    if RefundRequest.objects.filter(sale=sale, status='PENDING').exists():
        return JsonResponse({'success': False, 'error': 'A refund request for this sale is already pending approval.'}, status=400)

    if sale.status not in ['COMPLETED', 'PENDING_DISPATCH']:
        return JsonResponse({'success': False, 'error': 'Only completed or pending dispatch sales can be refunded.'}, status=400)

    try:
        data = json.loads(request.body)
        reason = data.get('reason', 'Customer requested refund')
    except Exception:
        reason = 'Customer requested refund'

    # Accountants and Admins refund immediately
    if role_name in ['ACCOUNTANT', 'ADMIN']:
        try:
            with transaction.atomic():
                sale.refund(reason=reason, user=request.user)
            return JsonResponse({'success': True, 'message': f'Sale {sale.sale_number} successfully refunded.'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    # Shop Managers — create a pending RefundRequest
    try:
        with transaction.atomic():
            refund_req = RefundRequest.objects.create(
                tenant=tenant,
                sale=sale,
                requested_by=request.user,
                reason=reason,
                status='PENDING',
            )

            # Notify all accountants + admins in this tenant
            from apps.notifications.models import Notification
            from apps.core.models import User as TenantUser

            approvers = TenantUser.objects.filter(
                tenant=tenant,
                role__name__in=['ACCOUNTANT', 'ADMIN'],
                is_active=True
            )
            for approver in approvers:
                Notification.objects.create(
                    tenant=tenant,
                    user=approver,
                    title="Refund Request Awaiting Approval",
                    message=(
                        f"{request.user.get_full_name() or request.user.email} has requested a refund "
                        f"for Sale {sale.sale_number} ({tenant.currency_symbol}{sale.total}) "
                        f"at {sale.shop.name}. "
                        f"Reason: {reason[:150]}"
                    ),
                    notification_type='SYSTEM',
                    reference_type='RefundRequest',
                    reference_id=refund_req.pk,
                )

        return JsonResponse({
            'success': True,
            'pending_approval': True,
            'refund_number': refund_req.refund_number,
            'message': (
                f'Refund request {refund_req.refund_number} submitted. '
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
    from .models import RefundRequest

    tenant = request.user.tenant
    role_name = request.user.role.name if request.user.role else None

    if role_name not in ['ACCOUNTANT', 'ADMIN']:
        return JsonResponse({'success': False, 'error': 'Only accountants and admins can approve refunds.'}, status=403)

    refund_req = get_object_or_404(RefundRequest, pk=pk, tenant=tenant)

    try:
        refund_req.approve(request.user)
        return JsonResponse({
            'success': True,
            'message': (
                f'Refund {refund_req.refund_number} approved. '
                f'Sale {refund_req.sale.sale_number} has been refunded.'
            ),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


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
        if role_name not in ['SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            messages.error(request, 'You do not have permission to view refund requests.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        from .models import RefundRequest
        user = self.request.user
        role_name = user.role.name if user.role else None
        tenant = user.tenant

        qs = RefundRequest.objects.filter(tenant=tenant).select_related(
            'sale', 'sale__shop', 'requested_by', 'reviewed_by'
        )

        if role_name == 'SHOP_MANAGER':
            qs = qs.filter(sale__shop=user.location)

        # Filter by status
        status_filter = self.request.GET.get('status', '')
        if status_filter:
            qs = qs.filter(status=status_filter)

        # Filter by shop
        shop_id = self.request.GET.get('shop')
        if shop_id and role_name != 'SHOP_MANAGER':
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
                When(status='APPROVED', then=1),
                When(status='REJECTED', then=2),
                default=3,
                output_field=IntegerField(),
            )
        ).order_by('status_order', '-created_at')

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import RefundRequest
        from apps.core.models import Location, User
        tenant = self.request.user.tenant
        role_name = self.request.user.role.name if self.request.user.role else None

        context['role_name'] = role_name
        context['pending_count'] = RefundRequest.objects.filter(tenant=tenant, status='PENDING').count()
        context['selected_status'] = self.request.GET.get('status', '')
        context['refund_always_cash'] = tenant.refund_always_cash
        
        context['shops'] = Location.objects.filter(tenant=tenant, location_type='SHOP')
        if role_name == 'SHOP_MANAGER':
            context['attendants'] = User.objects.filter(tenant=tenant, location=self.request.user.location, is_active=True).order_by('first_name', 'last_name')
        else:
            context['attendants'] = User.objects.filter(tenant=tenant, is_active=True).order_by('first_name', 'last_name')

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

    # ── PENDING INVOICE path (Cashier Workflow) ──────────────────────────────
    # Creates a PENDING sale without deducting stock. The cashier will later
    # complete the sale and trigger stock deduction at that point.
    if payment_method == 'PENDING_INVOICE':
        if not cart_items:
            return JsonResponse({'error': 'Cart is empty'}, status=400)
        if not customer_name or not customer_phone:
            return JsonResponse({'error': 'Customer name and phone are required for invoices'}, status=400)

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
    from django.db.models import F
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
        status__in=['COMPLETED', 'PENDING_DISPATCH']
    )

    if sale.is_dispatched:
        return JsonResponse({'error': 'This sale has already been fully dispatched.'}, status=400)

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
                if sale.status == 'PENDING_DISPATCH':
                    sale.status = 'COMPLETED'
                sale.is_dispatched = True
                sale.dispatched_at = timezone.now()
                sale.dispatched_by = request.user
                sale.save()
                status_msg = 'All goods dispatched. Sale completed.'
            else:
                # Partially dispatched — record who last dispatched and when
                sale.dispatched_by = request.user
                sale.dispatched_at = timezone.now()
                sale.save(update_fields=['dispatched_by', 'dispatched_at'])
                status_msg = 'Partial dispatch recorded successfully.'

        from django.urls import reverse
        return JsonResponse({
            'success': True,
            'sale_id': sale.pk,
            'sale_number': sale.sale_number,
            'fully_dispatched': fully_dispatched,
            'message': status_msg,
            'waybill_url': reverse('sales:sale_waybill', args=[sale.pk]) if fully_dispatched else None,
        })
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)




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
            status__in=['COMPLETED', 'PENDING_DISPATCH'],
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
            'sale__status__in': ['COMPLETED', 'PENDING_DISPATCH'],
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
        
        # Sales by day - always show
        context['sales_by_day'] = Sale.objects.filter(sales_filter).values(
            'created_at__date'
        ).annotate(
            revenue=Sum('total'),
            count=Count('id')
        ).order_by('-created_at__date')[:30]
        
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
                    
        # Generate predictable email based on phone to avoid Paystack fraud drops
        if phone:
            clean_phone = re.sub(r'\D', '', phone)
            customer_email = f'momo_{clean_phone}@hendaxis.com'
        else:
            unique_suffix = uuid.uuid4().hex[:8]
            customer_email = f'walkin_{unique_suffix}@hendaxis.com'
        
        if customer and customer.email and not phone:
            customer_email = customer.email
        
        # Generate unique reference
        reference = f"ECASH-{timezone.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"
        
        # For payment on account, we don't create a sale - just return Paystack config
        if is_payment_on_account:
            return JsonResponse({
                'success': True,
                'sale_id': None,  # No sale for payment on account
                'sale_number': None,
                'reference': reference,
                'paystack_public_key': provider.public_key,
                'customer_email': customer_email,
                'tenant_id': tenant.pk,
                'total': str(total),
                'is_payment_on_account': True,
                'customer_id': customer_id,
                'customer_name': customer.name if customer else None
            })
        
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
                # Create pending sale for normal cart checkout
                # Get current shift if any
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
                
                # Create sale items
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
            
        # Get checkout type and metadata
        checkout_type = provider.get_checkout_type
        authorization_url = ''
        
        # If it requires server-side initialization
        metadata = {'customer_number': data.get('phone', '')}
        if checkout_type != 'inline':
            init_result = provider.initialize_payment(
                amount=total if is_payment_on_account else sale.total,
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
        
        return JsonResponse({
            'success': True,
            'sale_id': None if is_payment_on_account else sale.pk,
            'sale_number': None if is_payment_on_account else sale.sale_number,
            'reference': reference,
            'public_key': getattr(provider, 'public_key', ''),
            'checkout_type': checkout_type,
            'authorization_url': authorization_url,
            'customer_email': customer_email,
            'phone': phone,
            'tenant_id': tenant.pk,
            'total': str(total if is_payment_on_account else sale.total),
            'provider': provider.provider_name
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
                    provider_config=provider.settings if hasattr(provider, 'settings') else None
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
        
        # Get the sale
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
                provider_config=provider.settings if hasattr(provider, 'settings') else None
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

@login_required
@require_POST
def api_sync_offline_sales(request):
    """
    Sync a single offline sale to the server.
    Uses client_sale_id for idempotency.
    """
    try:
        data = json.loads(request.body)
        user = request.user
        tenant = user.tenant

        client_sale_id = data.get('client_sale_id')
        if not client_sale_id:
            return JsonResponse({
                'success': False,
                'error': 'Missing client_sale_id for offline sync.'
            }, status=400)

        # Idempotency check - if this sale was already synced, return success
        existing = Sale.objects.filter(
            tenant=tenant,
            client_sale_id=client_sale_id
        ).first()
        if existing:
            return JsonResponse({
                'success': True,
                'sale_id': existing.pk,
                'sale_number': existing.sale_number,
                'message': 'Sale already synced (duplicate client_sale_id).',
                'already_synced': True,
            })

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
                return JsonResponse({'error': 'Customer required for payment on account'}, status=400)
            if amount_paid <= 0:
                return JsonResponse({'error': 'Payment amount must be positive'}, status=400)
            
            from apps.customers.models import Customer, CustomerTransaction
            customer = Customer.objects.filter(tenant=tenant, pk=customer_id).first()
            if not customer:
                return JsonResponse({'error': 'Customer not found'}, status=400)
                
            try:
                with transaction.atomic():
                    balance_before = customer.current_balance
                    customer.current_balance -= amount_paid
                    customer.save()
                    
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
                    
                    return JsonResponse({
                        'success': True,
                        'sale_id': txn.pk,
                        'sale_number': 'POA-' + str(txn.pk),
                        'has_conflicts': False,
                        'conflicts': [],
                        'message': 'Offline payment synced successfully.'
                    })
            except Exception as e:
                return JsonResponse({'error': str(e)}, status=400)

        if not cart_items:
            return JsonResponse({
                'success': False,
                'error': 'No items in offline sale.'
            }, status=400)

        # Only CASH, CREDIT and MOMO allowed for offline sales (no E-Cash)
        if payment_method not in ('CASH', 'CREDIT', 'MIXED', 'MOMO'):
            return JsonResponse({
                'success': False,
                'error': 'Only Cash, Credit or Momo payments are supported offline.'
            }, status=400)

        # Get shop
        shop = user.location
        if not shop or shop.location_type != 'SHOP':
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

        # Get customer if specified
        customer = None
        if customer_id:
            from apps.customers.models import Customer
            customer = Customer.objects.filter(
                tenant=tenant,
                pk=customer_id
            ).first()

        # Get current shift if any
        current_shift = Shift.objects.filter(
            tenant=tenant,
            attendant=user,
            status='OPEN'
        ).first()

        sync_conflicts = []

        with transaction.atomic():
            # Create the sale
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

            # Parse offline_created_at if provided
            if offline_created_at:
                from datetime import datetime
                try:
                    sale.offline_created_at = datetime.fromisoformat(
                        offline_created_at.replace('Z', '+00:00')
                    )
                except (ValueError, AttributeError):
                    pass  # Keep as None if parsing fails

            sale.save()

            # Create sale items and check for stock conflicts
            for item_data in cart_items:
                product = Product.objects.filter(
                    tenant=tenant,
                    pk=item_data.get('product_id'),
                    is_active=True
                ).first()

                if not product:
                    sync_conflicts.append(
                        f"Product ID {item_data.get('product_id')} not found or inactive."
                    )
                    continue

                # Check stock availability
                from apps.inventory.models import InventoryLedger
                from django.db.models import Sum
                quantity = Decimal(str(item_data.get('quantity', 0)))
                available = InventoryLedger.objects.filter(
                    tenant=tenant,
                    product=product,
                    location=shop
                ).aggregate(
                    total=Sum('quantity')
                )['total'] or Decimal('0')

                if available < quantity:
                    sync_conflicts.append(
                        f"{product.name}: requested {quantity}, available {available}"
                    )

                # Determine batch
                batch = None
                batch_id = item_data.get('batch_id')
                if batch_id:
                    from apps.inventory.models import Batch
                    batch = Batch.objects.filter(
                        tenant=tenant,
                        pk=batch_id
                    ).first()

                SaleItem.objects.create(
                    tenant=tenant,
                    sale=sale,
                    product=product,
                    batch=batch,
                    quantity=quantity,
                    unit_price=Decimal(str(item_data.get('unit_price', 0))),
                )

            # Calculate totals
            sale.calculate_totals()

            # Flag sync conflicts if any
            if sync_conflicts:
                sale.has_sync_conflict = True
                sale.sync_conflict_notes = '\n'.join(sync_conflicts)
                sale.save(update_fields=['has_sync_conflict', 'sync_conflict_notes'])

            # Complete the sale (deducts inventory)
            try:
                sale.complete(amount_paid, payment_method)
            except ValidationError as ve:
                # If completion fails (e.g., credit limit), flag as conflict
                sale.has_sync_conflict = True
                sale.sync_conflict_notes += f'\nCompletion error: {str(ve)}'
                sale.save(update_fields=['has_sync_conflict', 'sync_conflict_notes'])
                # Still mark as synced, but with conflict
                sync_conflicts.append(f'Completion error: {str(ve)}')

        return JsonResponse({
            'success': True,
            'sale_id': sale.pk,
            'sale_number': sale.sale_number,
            'has_conflicts': bool(sync_conflicts),
            'conflicts': sync_conflicts,
            'message': 'Offline sale synced successfully.' if not sync_conflicts
                       else 'Sale synced with conflicts - please review.',
        })

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
            tenant=user.tenant, shop=shop, status__in=['COMPLETED', 'PENDING_DISPATCH'],
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
            'sale__status__in': ['COMPLETED', 'PENDING_DISPATCH'],
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
    Shop Manager view to configure print layouts and receipts.
    (Excludes payment API keys to enforce separation of concerns).
    """
    model = ShopSettings
    form_class = ShopManagerSettingsForm
    template_name = 'sales/shop_settings.html'
    success_url = reverse_lazy('sales:shop_settings')
    
    def get_object(self, queryset=None):
        user = self.request.user
        shop = user.location
        
        if not shop or shop.location_type != 'SHOP':
            from apps.core.models import Location
            shop = Location.objects.filter(
                tenant=user.tenant,
                location_type='SHOP',
                is_active=True
            ).first()
            
        if not shop:
            from django.http import Http404
            raise Http404("No shop found for this tenant.")
            
        settings, _ = ShopSettings.objects.get_or_create(
            tenant=user.tenant,
            shop=shop,
            defaults={'receipt_printer_type': 'THERMAL_80MM'}
        )
        return settings

    def form_valid(self, form):
        messages.success(self.request, "Shop settings have been updated.")
        return super().form_valid(form)


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

    ALLOWED_ROLES = ['SHOP_MANAGER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN']

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
            status__in=['COMPLETED', 'PENDING_DISPATCH']
        )

        # Restrict shop managers to their own shop
        if role_name == 'SHOP_MANAGER':
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
        if role_name == 'SHOP_MANAGER':
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
        from django.db.models import F
        
        sales = Sale.objects.filter(
            tenant=tenant, 
            status__in=['COMPLETED', 'PENDING_DISPATCH']
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
