"""
Views for the sales app.
Includes POS interface and API endpoints for cart operations.
"""
import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.generic import ListView, DetailView, View
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
from apps.core.mixins import PaginationMixin


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
                
                products_with_prices.append({
                    'id': product.pk,
                    'name': product.name,
                    'sku': product.sku or '',
                    'category': product.category.name if product.category else 'Uncategorized',
                    'price': str(shop_price.selling_price),
                    'unit': product.unit_of_measure,
                    'quantity': float(quantity),
                    'threshold': float(product.reorder_level),
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


class SaleListView(LoginRequiredMixin, PaginationMixin, ListView):
    """List sales for the shop."""
    model = Sale
    template_name = 'sales/sale_list.html'
    context_object_name = 'sales'
    
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
        
        return queryset
    
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
                is_active=True
            ).exclude(role__name__in=['AUDITOR', 'ACCOUNTANT']).order_by('first_name', 'email')
        
        # Preserve filter values
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')
        context['selected_shop'] = self.request.GET.get('shop', '')
        context['selected_attendant'] = self.request.GET.get('attendant', '')
        context['selected_status'] = self.request.GET.get('status', '')
        context['selected_payment'] = self.request.GET.get('payment', '')
        
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
    valid_payment_methods = ['CASH', 'CREDIT', 'ECASH', 'MIXED', 'PAYMENT_ON_ACCOUNT']
    if not payment_method or payment_method not in valid_payment_methods:
        payment_method = 'CASH'
    amount_paid = Decimal(str(data.get('amount_paid', 0)))
    discount_amount = Decimal(str(data.get('discount_amount', 0)))
    discount_reason = data.get('discount_reason', '')
    paystack_ref = data.get('paystack_reference', '')
    customer_id = data.get('customer_id')
    is_payment_on_account = data.get('is_payment_on_account', False)
    
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
        
        # Get payment provider settings
        from apps.payments.models import PaymentProviderSettings
        provider_settings = PaymentProviderSettings.objects.filter(
            tenant=tenant,
            provider='PAYSTACK',
            is_active=True
        ).first()
        
        if not provider_settings:
            return JsonResponse({
                'success': False,
                'error': 'E-Cash payment is not configured. Please contact admin.'
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
                'paystack_public_key': provider_settings.public_key,
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
            'paystack_public_key': provider_settings.public_key,
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
        
        # Verify with Paystack
        from apps.payments.services.paystack import get_payment_provider
        provider = get_payment_provider(tenant)
        
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
