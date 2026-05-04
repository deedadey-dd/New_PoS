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
from django.db.models import Q
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
        
        # Strict workflow manager restriction
        if request.user.tenant.use_strict_sales_workflow and request.user.role and request.user.role.name == 'SHOP_MANAGER':
            messages.warning(request, "Shop Managers cannot access the POS when Strict Sales Workflow is enabled. Please use the Dispatch view.")
            return redirect('core:dashboard')
            
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
        
        context = {
            'shop': user_shop,
            'shop_settings': shop_settings,
            'shift': open_shift,
            'products': json.dumps(products_with_prices),
            'customers': json.dumps(list(customers), default=str),
            'categories': categories,
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
        credit_sales = sales_qs.filter(payment_method='CREDIT').aggregate(
            total=Sum('total'))['total'] or Decimal('0')
        mixed_sales = sales_qs.filter(payment_method='MIXED').aggregate(
            total=Sum('amount_paid'))['total'] or Decimal('0')  # Only cash portion
        
        all_sales = sales_qs.aggregate(total=Sum('total'))['total'] or Decimal('0')
        total_cash = shift.opening_cash + cash_sales + mixed_sales
        
        return render(request, self.template_name, {
            'shift': shift,
            'expected_cash': total_cash,  # Opening + Cash Sales portion
            'total_sales': all_sales,  # All sales
            'cash_sales': cash_sales + mixed_sales,  # Cash portion only
            'ecash_sales': ecash_sales,
            'credit_sales': credit_sales,
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
                # Shop manager's shift - no transfer needed, cash goes directly to their balance
                # Create a confirmed transfer to self for record-keeping
                transfer = CashTransfer.objects.create(
                    tenant=request.user.tenant,
                    amount=closing_cash,
                    transfer_type='DEPOSIT',
                    from_user=request.user,
                    from_location=shift.shop,
                    to_user=request.user,
                    to_location=shift.shop,
                    notes=f"Shop Manager shift closing - Shift #{shift.pk}",
                    status='CONFIRMED'  # Auto-confirmed
                )
                transfer.confirmed_at = timezone.now()
                transfer.save()
                
                messages.info(request, f"Shift cash of {closing_cash} added to your cash on hand.")
            else:
                # Attendant shift - create pending transfer to shop manager
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
                    messages.warning(request, "No shop manager found. Please manually transfer your cash.")
        
        return redirect('core:dashboard')


class SaleListView(LoginRequiredMixin, SortableMixin, ListView):
    """List sales for the shop."""
    model = Sale
    template_name = 'sales/sale_list.html'
    context_object_name = 'sales'
    sortable_fields = ['created_at', 'sale_number', 'total', 'amount_paid', 'status', 'payment_method', 'attendant__username']
    default_sort = '-created_at'
    
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
            
            # Shop Attendants only see their OWN invoices
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
            
        # Dispatched filter
        dispatched = self.request.GET.get('dispatched')
        if dispatched == '1':
            queryset = queryset.filter(is_dispatched=True)
        elif dispatched == '0':
            queryset = queryset.filter(is_dispatched=False)
        
        # Payment method filter
        payment = self.request.GET.get('payment')
        if payment:
            queryset = queryset.filter(payment_method=payment)
        
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
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['selected_shop'] = self.request.GET.get('shop', '')
        context['selected_attendant'] = self.request.GET.get('attendant', '')
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_payment'] = self.request.GET.get('payment', '')
        context['selected_dispatched'] = self.request.GET.get('dispatched', '')
        
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
        
        # Determine number of receipt copies to print (shop setting overrides tenant)
        shop_settings = context.get('shop_settings')
        if shop_settings and shop_settings.receipt_print_copies:
            copies = shop_settings.receipt_print_copies
        else:
            copies = getattr(self.request.user.tenant, 'receipt_print_copies', 1)
        context['print_copies'] = range(copies)
        
        return context


@login_required
def api_sale_detail(request, pk):
    """Return sale detail as JSON for modal display."""
    sale = get_object_or_404(
        Sale.objects.select_related('shop', 'attendant', 'customer')
                    .prefetch_related('items__product'),
        pk=pk,
        tenant=request.user.tenant,
    )
    items = []
    for item in sale.items.all():
        items.append({
            'product_name': item.product.name,
            'sku': item.product.sku,
            'quantity': str(item.quantity),
            'unit_price': str(item.unit_price),
            'total': str(item.total),
        })

    data = {
        'id': sale.id,
        'sale_number': sale.sale_number,
        'created_at': sale.created_at.strftime('%b %d, %Y %H:%M'),
        'shop': sale.shop.name,
        'attendant': sale.attendant.get_full_name() or sale.attendant.email,
        'payment_method': sale.get_payment_method_display(),
        'payment_method_code': sale.payment_method,
        'status': sale.get_status_display(),
        'status_code': sale.status,
        'is_dispatched': sale.is_dispatched,
        'subtotal': str(sale.subtotal),
        'discount_amount': str(sale.discount_amount) if sale.discount_amount else None,
        'tax_amount': str(sale.tax_amount) if sale.tax_amount else None,
        'total': str(sale.total),
        'amount_paid': str(sale.amount_paid),
        'change_given': str(sale.change_given) if sale.change_given else None,
        'customer': sale.customer.name if sale.customer else None,
        'customer_phone': sale.customer.phone if sale.customer else None,
        'walkin_customer_name': sale.walkin_customer_name or None,
        'walkin_customer_phone': sale.walkin_customer_phone or None,
        'items': items,
        'is_admin': request.user.role.name == 'ADMIN' if request.user.role else False,
    }
    return JsonResponse(data)


# ============ API Views for POS ============

@login_required
def api_product_search(request):
    """Search products for POS - respects hide_zero_stock and returns stock quantities."""
    from django.db.models import Q, Prefetch, Sum
    from apps.inventory.models import InventoryLedger

    query = request.GET.get('q', '')
    shop = request.user.location

    if not shop or shop.location_type != 'SHOP':
        return JsonResponse({'products': []})

    # Check shop settings
    try:
        shop_settings = ShopSettings.objects.get(tenant=request.user.tenant, shop=shop)
        hide_zero_stock = shop_settings.hide_zero_stock_items
        warn_low_stock = shop_settings.warn_on_low_stock
    except ShopSettings.DoesNotExist:
        hide_zero_stock = False
        warn_low_stock = True

    # Prefetch shop prices for THIS shop only
    shop_price_prefetch = Prefetch(
        'shop_prices',
        queryset=ShopPrice.objects.filter(location=shop, is_active=True),
        to_attr='current_shop_prices'
    )

    products_qs = Product.objects.filter(
        tenant=request.user.tenant,
        is_active=True
    ).filter(
        Q(name__icontains=query) |
        Q(sku__icontains=query) |
        Q(barcode__icontains=query)
    ).prefetch_related(shop_price_prefetch)[:50]

    # Build stock lookup for the shop in one query
    stock_map = {}
    ledger_agg = InventoryLedger.objects.filter(
        tenant=request.user.tenant,
        location=shop
    ).values('product_id').annotate(total=Sum('quantity'))
    for row in ledger_agg:
        stock_map[row['product_id']] = float(row['total'] or 0)

    results = []
    for product in products_qs:
        shop_prices = getattr(product, 'current_shop_prices', [])
        if not shop_prices:
            # Use default product price if no shop price set
            price = str(product.default_selling_price or '0')
        else:
            price = str(shop_prices[0].selling_price)

        stock_qty = stock_map.get(product.pk, 0)

        if hide_zero_stock and stock_qty <= 0:
            continue

        results.append({
            'id': product.pk,
            'name': product.name,
            'sku': product.sku,
            'price': price,
            'unit': product.unit_of_measure,
            'stock_qty': stock_qty,
            'warn_low_stock': warn_low_stock,
        })
        if len(results) >= 20:
            break

    return JsonResponse({'products': results})


@login_required
def api_customer_search(request):
    """
    Search customers (CRM + historical walk-ins) by name or phone
    """
    query = request.GET.get('q', '').strip()
    if len(query) < 2:
        return JsonResponse({'results': []})
        
    tenant = request.user.tenant
    results = []
    
    # 1. Search CRM Customers
    from apps.customers.models import Customer
    customers = Customer.objects.filter(
        tenant=tenant,
        is_active=True
    ).filter(
        Q(name__icontains=query) |
        Q(phone__icontains=query) |
        Q(email__icontains=query)
    )[:10]
    
    for c in customers:
        results.append({
            'type': 'Registered',
            'id': c.id,
            'name': c.name,
            'phone': c.phone or '',
            'balance': str(c.financial_balance)
        })
        
    # 2. Search historical walk-ins from sales
    walkins = Sale.objects.filter(
        tenant=tenant,
        customer__isnull=True
    ).filter(
        Q(walkin_customer_name__icontains=query) | 
        Q(walkin_customer_phone__icontains=query)
    ).values('walkin_customer_name', 'walkin_customer_phone').distinct()[:10]
    
    existing_phones = {r['phone'] for r in results if r['phone']}
    
    for w in walkins:
        phone = w['walkin_customer_phone']
        if phone and phone not in existing_phones:
            results.append({
                'type': 'Walk-in',
                'id': '',
                'name': w['walkin_customer_name'] or 'Walk-in Customer',
                'phone': phone,
            })
            existing_phones.add(phone)
            
    # Limit to 15
    return JsonResponse({'results': results[:15]})


class CashierPendingInvoicesView(LoginRequiredMixin, ListView):
    template_name = 'sales/cashier_invoices.html'
    context_object_name = 'invoices'

    def get_queryset(self):
        tenant = self.request.user.tenant
        qs = Sale.objects.filter(
            Q(status='PENDING') | Q(is_disputed=True),
            tenant=tenant
        ).select_related('attendant', 'customer', 'shop').prefetch_related('items__product').order_by('-created_at')

        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(sale_number__icontains=q) |
                Q(walkin_customer_name__icontains=q) |
                Q(walkin_customer_phone__icontains=q) |
                Q(customer__name__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['q'] = self.request.GET.get('q', '')
        return ctx


class ManagerDispatchView(LoginRequiredMixin, AdminOrManagerRequiredMixin, ListView):
    template_name = 'sales/dispatch_invoices.html'
    context_object_name = 'invoices'
    paginate_by = 20
    
    def get_queryset(self):
        from datetime import datetime
        tenant = self.request.user.tenant
        qs = Sale.objects.filter(
            tenant=tenant, 
            status='COMPLETED'
        ).select_related('attendant', 'customer').order_by('-created_at')
        
        # Date range filter
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        
        if date_from:
            try:
                date_from_parsed = datetime.strptime(date_from, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__gte=date_from_parsed)
            except ValueError:
                pass
        if date_to:
            try:
                date_to_parsed = datetime.strptime(date_to, '%Y-%m-%d').date()
                qs = qs.filter(created_at__date__lte=date_to_parsed)
            except ValueError:
                pass
                
        # Dispatch Status filter
        status = self.request.GET.get('dispatch_status')
        if status == 'DISPATCHED':
            qs = qs.filter(is_dispatched=True)
        elif status == 'AWAITING':
            qs = qs.filter(is_dispatched=False)
            
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Preserve filters
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['dispatch_status'] = self.request.GET.get('dispatch_status', '')
        # Only Strict workflow has Dispatch
        context['use_strict_workflow'] = getattr(self.request.user.tenant, 'use_strict_sales_workflow', False)
        return context


@login_required
@require_POST
def api_pay_invoice(request):
    try:
        data = json.loads(request.body)
        sale_id = data.get('sale_id')
        payment_method = data.get('payment_method', 'CASH')
        amount_paid = Decimal(str(data.get('amount_paid', 0)))
        paystack_ref = data.get('paystack_reference', '')
        
        sale = Sale.objects.get(pk=sale_id, tenant=request.user.tenant, status='PENDING')
        
        with transaction.atomic():
            if payment_method == 'CREDIT' and not sale.customer:
                 return JsonResponse({'error': 'Credit sales require a registered customer.'}, status=400)
                 
            sale.cashier = request.user
            sale.complete(amount_paid, payment_method, paystack_ref)
            
            # Reset dispute status if it was previously disputed
            if getattr(sale, 'is_disputed', False):
                sale.is_disputed = False
                sale.save(update_fields=['is_disputed'])
            
            if payment_method in ['ECASH', 'MOMO']:
                from apps.payments.models import ECashLedger
                actual_paid = min(amount_paid, sale.total)
                ECashLedger.record_payment(
                    tenant=request.user.tenant,
                    amount=actual_paid,
                    sale=sale,
                    paystack_ref=paystack_ref,
                    user=request.user,
                    wallet_type=payment_method
                )
                
            # Notify Shop Managers that invoice is paid and ready for dispatch
            from apps.notifications.models import Notification
            managers = sale.shop.users.filter(role__name='SHOP_MANAGER')
            for manager in managers:
                Notification.objects.create(
                    tenant=request.user.tenant,
                    user=manager,
                    title="Invoice Paid & Ready for Dispatch",
                    message=f"Invoice {sale.sale_number} has been paid and is ready to be dispatched.",
                    notification_type='INVOICE_PAID',
                    reference_type='Sale',
                    reference_id=sale.pk
                )
            
        return JsonResponse({'success': True, 'sale_id': sale_id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_dispatch_invoice(request):
    try:
        data = json.loads(request.body)
        sale_id = data.get('sale_id')
        
        if request.user.role.name not in ['SHOP_MANAGER', 'ADMIN']:
             return JsonResponse({'error': 'Permission denied.'}, status=403)
             
        sale = Sale.objects.get(pk=sale_id, tenant=request.user.tenant, status='COMPLETED', is_dispatched=False)
        
        with transaction.atomic():
            sale.dispatch(request.user)
            
            # Notify Attendant that the invoice has been dispatched
            from apps.notifications.models import Notification
            Notification.objects.create(
                tenant=request.user.tenant,
                user=sale.attendant,
                title="Invoice Dispatched",
                message=f"Invoice {sale.sale_number} has been dispatched. You can hand over the items to the customer.",
                notification_type='INVOICE_DISPATCHED',
                reference_type='Sale',
                reference_id=sale.pk
            )
            
        return JsonResponse({'success': True, 'sale_id': sale_id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_delete_invoices(request):
    """Bulk delete PENDING invoices."""
    try:
        data = json.loads(request.body)
        invoice_ids = data.get('invoice_ids', [])
        
        if not invoice_ids:
            return JsonResponse({'error': 'No invoices selected.'}, status=400)
            
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['SHOP_CASHIER', 'SHOP_MANAGER', 'ADMIN', 'ACCOUNTANT']:
            return JsonResponse({'error': 'Permission denied.'}, status=403)
            
        deleted_count = Sale.objects.filter(
            pk__in=invoice_ids, 
            tenant=request.user.tenant, 
            status='PENDING'
        ).delete()[0]
        
        return JsonResponse({'success': True, 'deleted': deleted_count})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


class InvoiceEditView(LoginRequiredMixin, DetailView):
    """View to load an editable interface for a PENDING invoice."""
    model = Sale
    template_name = 'sales/invoice_edit.html'
    context_object_name = 'invoice'
    
    def get_queryset(self):
        return Sale.objects.filter(tenant=self.request.user.tenant, status='PENDING')


@login_required
@require_POST
def api_update_invoice(request, pk):
    """Update a PENDING invoice with new items/quantities."""
    try:
        data = json.loads(request.body)
        sale = get_object_or_404(Sale, pk=pk, tenant=request.user.tenant, status='PENDING')
        
        role_name = request.user.role.name if request.user.role else None
        if role_name not in ['SHOP_CASHIER', 'SHOP_MANAGER', 'ADMIN', 'ACCOUNTANT']:
            return JsonResponse({'error': 'Permission denied.'}, status=403)
            
        cart_items = data.get('items', [])
        if not cart_items:
            return JsonResponse({'error': 'Invoice cannot be empty.'}, status=400)
            
        with transaction.atomic():
            # Delete existing items
            sale.items.all().delete()
            
            # Recreate items
            for item in cart_items:
                product = Product.objects.get(pk=item['product_id'], tenant=request.user.tenant)
                quantity = Decimal(str(item['quantity']))
                unit_price = Decimal(str(item['unit_price']))
                
                SaleItem.objects.create(
                    tenant=request.user.tenant,
                    sale=sale,
                    product=product,
                    quantity=quantity,
                    unit_price=unit_price,
                )
                
            sale.calculate_totals()
            
            return JsonResponse({
                'success': True, 
                'total': str(sale.total)
            })
            
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def invoice_print_view(request, pk):
    """Printable view for a PENDING invoice."""
    sale = get_object_or_404(Sale, pk=pk, tenant=request.user.tenant, status='PENDING')
    
    # Get shop settings for receipt header/footer
    shop_settings = None
    if getattr(sale.shop, 'shop_settings', None):
        shop_settings = sale.shop.shop_settings
        
    return render(request, 'sales/invoice_print.html', {
        'sale': sale,
        'shop_settings': shop_settings
    })


@login_required
def api_complete_sale(request):
    """Complete a sale via AJAX."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    cart_items = data.get('items', [])
    payment_method = data.get('payment_method', 'CASH')
    # Ensure payment_method is valid - handle empty string or invalid values
    valid_payment_methods = ['CASH', 'CREDIT', 'ECASH', 'MOMO', 'MIXED', 'PAYMENT_ON_ACCOUNT']
    if not payment_method or payment_method not in valid_payment_methods:
        payment_method = 'CASH'
    amount_paid = Decimal(str(data.get('amount_paid', 0)))
    discount_amount = Decimal(str(data.get('discount_amount', 0)))
    discount_reason = data.get('discount_reason', '')
    paystack_ref = data.get('paystack_reference', '')
    customer_id = data.get('customer_id')
    is_payment_on_account = data.get('is_payment_on_account', False)
    walkin_customer_name = data.get('walkin_customer_name', '')
    walkin_customer_phone = data.get('walkin_customer_phone', '')
    
    shop = request.user.location
    
    if not shop or shop.location_type != 'SHOP':
        return JsonResponse({'error': 'No shop assigned'}, status=400)
    
    # Get customer if specified
    customer = None
    if customer_id:
        from apps.customers.models import Customer, CustomerTransaction
        customer = Customer.objects.filter(pk=customer_id, tenant=request.user.tenant).first()
    
    # Handle payment on account (no cart items, just payment to customer)
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
                    description=f"Payment on account",
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
            
            tenant = request.user.tenant
            role_name = request.user.role.name if request.user.role else None
            if tenant.use_strict_sales_workflow and role_name in ('SHOP_ATTENDANT', 'SHOP_CASHIER'):
                # Attendant/Cashier creates PENDING invoice — validate customer info first
                if not customer and (not walkin_customer_name or not walkin_customer_phone):
                    return JsonResponse({
                        'error': 'Customer name and phone number are required in strict sales mode.'
                    }, status=400)
                # Save walk-in info on the sale
                sale.walkin_customer_name = walkin_customer_name
                sale.walkin_customer_phone = walkin_customer_phone
                sale.save(update_fields=['walkin_customer_name', 'walkin_customer_phone'])
                
                # Notify Shop Cashiers that a new pending invoice is ready
                from apps.notifications.models import Notification
                cashiers = sale.shop.users.filter(role__name='SHOP_CASHIER')
                for cashier in cashiers:
                    Notification.objects.create(
                        tenant=tenant,
                        user=cashier,
                        title="New Pending Invoice",
                        message=f"Invoice {sale.sale_number} is waiting for payment.",
                        notification_type='INVOICE_PENDING',
                        reference_type='Sale',
                        reference_id=sale.pk
                    )
                
                # Stay PENDING — Cashier handles payment separately
                return JsonResponse({
                    'success': True,
                    'sale_id': sale.pk,
                    'sale_number': sale.sale_number,
                    'status': 'PENDING',
                    'total': str(sale.total),
                    'message': 'Invoice created. Give the code to the customer and direct them to the Cashier.'
                })
            
            # Non-strict workflow or Manager creating sale directly
            sale.cashier = request.user
            
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
                
                if payment_method in ['ECASH', 'MOMO']:
                    from apps.payments.models import ECashLedger
                    ECashLedger.record_payment(
                        tenant=request.user.tenant,
                        amount=sale.total,
                        sale=sale,
                        paystack_ref=paystack_ref,
                        user=request.user,
                        wallet_type=payment_method
                    )
            else:
                # Complete sale (handles partial payments)
                sale.complete(amount_paid, payment_method, paystack_ref)
                
                if payment_method in ['ECASH', 'MOMO']:
                    from apps.payments.models import ECashLedger
                    actual_paid = min(amount_paid, sale.total)  # In case of change, though digital rarely has change
                    ECashLedger.record_payment(
                        tenant=request.user.tenant,
                        amount=actual_paid,
                        sale=sale,
                        paystack_ref=paystack_ref,
                        user=request.user,
                        wallet_type=payment_method
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
def api_revert_payment(request, pk):
    """Revert a COMPLETED sale back to PENDING (Admin only)."""
    if request.user.role.name != 'ADMIN':
        return JsonResponse({'error': 'Permission denied. Admin access required.'}, status=403)
        
    sale = get_object_or_404(Sale, pk=pk, tenant=request.user.tenant)
    
    if sale.status != 'COMPLETED':
        return JsonResponse({'error': 'Only COMPLETED sales can be reverted.'}, status=400)
    
    if sale.is_dispatched:
        return JsonResponse({'error': 'Sale has already been dispatched. Revert the dispatch first.'}, status=400)
        
    try:
        from django.db import transaction
        with transaction.atomic():
            # If there was a customer debt transaction, reverse it
            if sale.customer:
                from apps.customers.models import CustomerTransaction
                # Find the exact debit transaction generated by this sale
                debt_tx = CustomerTransaction.objects.filter(
                    tenant=sale.tenant,
                    customer=sale.customer,
                    reference_id=sale.sale_number,
                    transaction_type='DEBIT'
                ).first()
                
                if debt_tx:
                    balance_before = sale.customer.current_balance
                    sale.customer.current_balance -= debt_tx.amount
                    sale.customer.save()
                    
                    CustomerTransaction.objects.create(
                        tenant=sale.tenant,
                        customer=sale.customer,
                        transaction_type='CREDIT',
                        amount=debt_tx.amount,
                        description=f"Reversed Payment (Sale {sale.sale_number})",
                        reference_id=sale.sale_number,
                        balance_before=balance_before,
                        balance_after=sale.customer.current_balance,
                        performed_by=request.user
                    )
            
            # Reset sale
            sale.status = 'PENDING'
            sale.completed_at = None
            sale.amount_paid = 0
            sale.change_given = 0
            sale.payment_method = ''
            sale.save()
            
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def api_revert_dispatch(request, pk):
    """Revert a dispatched sale back to non-dispatched (Admin only)."""
    if request.user.role.name != 'ADMIN':
        return JsonResponse({'error': 'Permission denied. Admin access required.'}, status=403)
        
    sale = get_object_or_404(Sale, pk=pk, tenant=request.user.tenant)
    
    if not sale.is_dispatched:
        return JsonResponse({'error': 'Sale has not been dispatched.'}, status=400)
        
    try:
        from django.db import transaction
        with transaction.atomic():
            from apps.inventory.models import InventoryLedger
            
            # Reverse all inventory ledgers for this sale
            ledgers = InventoryLedger.objects.filter(
                tenant=sale.tenant,
                reference_type='Sale',
                reference_id=sale.pk
            )
            
            for ledger in ledgers:
                # Add a compensating ledger to restore stock
                InventoryLedger.objects.create(
                    tenant=ledger.tenant,
                    product=ledger.product,
                    batch=ledger.batch,
                    location=ledger.location,
                    transaction_type='SALE_VOID',  # Re-injects stock functionally identically
                    quantity=-ledger.quantity,     # If ledger was -2, this is +2
                    unit_cost=ledger.unit_cost,
                    reference_type='Sale Revert',
                    reference_id=sale.pk,
                    notes=f"Dispatch Reverted (Sale {sale.sale_number})",
                    created_by=request.user
                )
            
            sale.is_dispatched = False
            sale.dispatched_by = None
            sale.dispatched_at = None
            sale.save()
            
        return JsonResponse({'success': True})
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
        
        # Allow shop managers, cashiers, accountants, auditors, and admin to access
        if role_name not in ['SHOP_MANAGER', 'SHOP_CASHIER', 'ACCOUNTANT', 'AUDITOR', 'ADMIN']:
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
            status='COMPLETED',
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
            'sale__status': 'COMPLETED',
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
        
        # Get active payment provider (handles shop-level overrides)
        from apps.payments.services.paystack import get_payment_provider
        provider = get_payment_provider(tenant, shop=shop)
        
        if not provider or not provider.public_key:
            return JsonResponse({
                'success': False,
                'error': 'E-Cash payment is not configured for this shop. Please contact admin.'
            }, status=400)
        
        items = data.get('items', [])
        discount = Decimal(str(data.get('discount_amount', 0)))
        customer_id = data.get('customer_id')
        total = Decimal(str(data.get('total', 0)))
        is_payment_on_account = data.get('is_payment_on_account', False)
        
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
        elif not items or total <= 0:
            return JsonResponse({
                'success': False,
                'error': 'Invalid cart data.'
            }, status=400)
        
        # Shop is already retrieved up top
        
        # Get customer if specified
        customer = None
        customer_email = 'customer@example.com'
        if customer_id:
            from apps.customers.models import Customer
            customer = Customer.objects.filter(
                tenant=tenant,
                pk=customer_id
            ).first()
            if customer and customer.email:
                customer_email = customer.email
        
        # Generate unique reference
        import uuid
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
        
        # Create pending sale for normal cart checkout
        with transaction.atomic():
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
            
            # Calculate totals
            sale.calculate_totals()
        
        return JsonResponse({
            'success': True,
            'sale_id': sale.pk,
            'sale_number': sale.sale_number,
            'reference': reference,
            'paystack_public_key': provider.public_key,
            'customer_email': customer_email,
            'tenant_id': tenant.pk,
            'total': str(sale.total)
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
        
        # Get shop and provider
        shop = user.location
        if not shop or shop.location_type != 'SHOP':
            from apps.core.models import Location
            shop = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True).first()
            
        from apps.payments.services.paystack import get_payment_provider
        provider = get_payment_provider(tenant, shop=shop)
        
        if not provider:
            return JsonResponse({
                'success': False,
                'error': 'Payment provider not configured.'
            }, status=400)
        
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
                    description=f"E-Cash Payment (Paystack: {reference[:20]}...)",
                    reference_id=reference,
                    balance_before=balance_before,
                    balance_after=customer.current_balance,
                    performed_by=user
                )
                
                # Record in e-cash ledger
                from apps.payments.models import ECashLedger
                ECashLedger.record_payment(
                    tenant=tenant,
                    amount=amount,
                    sale=None,
                    paystack_ref=reference,
                    user=user,
                    notes=f"Payment on account for {customer.name}"
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
            from apps.payments.models import ECashLedger
            ECashLedger.record_payment(
                tenant=tenant,
                amount=sale.total,
                sale=sale,
                paystack_ref=reference,
                user=user
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

        if not cart_items:
            return JsonResponse({
                'success': False,
                'error': 'No items in offline sale.'
            }, status=400)

        # Only CASH and CREDIT allowed for offline sales (no E-Cash)
        if payment_method not in ('CASH', 'CREDIT', 'MIXED'):
            return JsonResponse({
                'success': False,
                'error': 'Only Cash or Credit payments are supported offline.'
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
            tenant=user.tenant, shop=shop, status='COMPLETED',
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

        wb = create_export_workbook('Attendant Breakdown', att_headers, att_rows)

        # Sheet 2: Product Breakdown
        items_filter = {
            'sale__tenant': user.tenant,
            'sale__shop': shop,
            'sale__status': 'COMPLETED',
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



# ============ PDF Receipt and Waybill ============

@login_required
def sale_receipt_pdf(request, pk):
    """Generate and download a PDF version of a sale receipt using xhtml2pdf."""
    from django.template.loader import get_template
    from django.http import HttpResponse

    sale = get_object_or_404(
        Sale.objects.select_related('shop', 'attendant', 'cashier', 'customer')
                    .prefetch_related('items__product'),
        pk=pk,
        tenant=request.user.tenant,
    )

    try:
        shop_settings = ShopSettings.objects.get(tenant=request.user.tenant, shop=sale.shop)
    except ShopSettings.DoesNotExist:
        shop_settings = None

    context = {
        'sale': sale,
        'shop_settings': shop_settings,
        'tenant': request.user.tenant,
        'pdf_mode': True,
    }

    try:
        from xhtml2pdf import pisa
        import io

        template = get_template('sales/sale_receipt_pdf.html')
        html_string = template.render(context)

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="receipt_{sale.sale_number}.pdf"'

        pisa_status = pisa.CreatePDF(io.BytesIO(html_string.encode('utf-8')), dest=response)
        if pisa_status.err:
            return HttpResponse('PDF generation failed.', status=500)
        return response

    except ImportError:
        messages.error(request, 'PDF generation library (xhtml2pdf) is not installed. Please contact your system administrator.')
        return redirect('sales:sale_receipt', pk=pk)


@login_required
def waybill_print_view(request, pk):
    """Render a printable waybill for a dispatched sale."""
    role_name = request.user.role.name if request.user.role else ''
    if role_name not in ['SHOP_MANAGER', 'ADMIN', 'AUDITOR', 'ACCOUNTANT']:
        messages.error(request, 'You do not have permission to print waybills.')
        return redirect('core:dashboard')

    sale = get_object_or_404(
        Sale.objects.select_related('shop', 'attendant', 'cashier', 'customer')
                    .prefetch_related('items__product'),
        pk=pk,
        tenant=request.user.tenant,
    )

    context = {
        'sale': sale,
        'tenant': request.user.tenant,
    }
    return render(request, 'sales/waybill_print.html', context)
