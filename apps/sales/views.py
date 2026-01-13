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


class POSView(LoginRequiredMixin, View):
    """Main POS interface."""
    template_name = 'sales/pos.html'
    
    def get(self, request):
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
        
        # Get products with shop prices
        products = Product.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        ).select_related('category').prefetch_related('shop_prices')
        
        # Get products with prices for this shop (ShopPrice required)
        products_with_prices = []
        for product in products:
            shop_price = product.shop_prices.filter(
                location=user_shop, is_active=True
            ).first()
            
            # Only include products with shop-specific pricing
            if shop_price:
                # Get quantity at this location
                quantity = product.get_stock_at_location(user_shop)
                
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
        from apps.inventory.models import Category
        categories = Category.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        )

        # Get customers for POS search
        from apps.customers.models import Customer
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
        
        return render(request, self.template_name, {
            'shift': shift,
            'expected_cash': shift.expected_cash,
            'total_sales': shift.total_sales,
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
                    notes=f"Shift closing deposit - {shift.sale_number if hasattr(shift, 'sale_number') else f'Shift #{shift.pk}'}"
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


class SaleListView(LoginRequiredMixin, ListView):
    """List sales for the shop."""
    model = Sale
    template_name = 'sales/sale_list.html'
    context_object_name = 'sales'
    
    def get_queryset(self):
        queryset = Sale.objects.filter(
            tenant=self.request.user.tenant
        ).select_related('shop', 'attendant')
        
        # Filter by shop if user is shop-based
        if self.request.user.location and self.request.user.location.location_type == 'SHOP':
            queryset = queryset.filter(shop=self.request.user.location)
        
        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        return queryset[:100]  # Limit to last 100


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
    """Search products for POS."""
    query = request.GET.get('q', '')
    shop = request.user.location
    
    if not shop or shop.location_type != 'SHOP':
        return JsonResponse({'products': []})
    
    products = Product.objects.filter(
        tenant=request.user.tenant,
        is_active=True
    ).filter(
        models.Q(name__icontains=query) | 
        models.Q(sku__icontains=query) |
        models.Q(barcode__icontains=query)
    )[:20]
    
    results = []
    for product in products:
        shop_price = ShopPrice.objects.filter(
            product=product,
            location=shop,
            is_active=True
        ).first()
        
        if shop_price:
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
        from datetime import timedelta
        
        user = request.user
        role_name = user.role.name if user.role else None
        
        # Only shop managers and admin can access
        if role_name not in ['SHOP_MANAGER', 'ADMIN']:
            messages.error(request, 'You do not have permission to view this report.')
            return redirect('core:dashboard')
        
        # Get the shop
        shop = user.location
        if not shop and role_name == 'ADMIN':
            # Admin can select a shop
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
        
        # Date range filter
        date_range = request.GET.get('range', 'today')
        today = timezone.now().date()
        
        if date_range == 'week':
            start_date = today - timedelta(days=7)
            context['date_range_label'] = 'Last 7 Days'
        elif date_range == 'month':
            start_date = today - timedelta(days=30)
            context['date_range_label'] = 'Last 30 Days'
        else:
            start_date = today
            context['date_range_label'] = 'Today'
        
        context['current_range'] = date_range
        
        # Get sales by attendant
        sales_filter = Q(
            tenant=user.tenant,
            shop=shop,
            status='COMPLETED',
            created_at__date__gte=start_date
        )
        
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
        
        # Top products sold
        context['top_products'] = SaleItem.objects.filter(
            sale__tenant=user.tenant,
            sale__shop=shop,
            sale__status='COMPLETED',
            sale__created_at__date__gte=start_date
        ).values('product__id', 'product__name').annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total')
        ).order_by('-revenue')[:5]
        
        # Sales by day (for week/month view)
        if date_range != 'today':
            context['sales_by_day'] = Sale.objects.filter(sales_filter).values(
                'created_at__date'
            ).annotate(
                revenue=Sum('total'),
                count=Count('id')
            ).order_by('-created_at__date')[:7]
        
        # Recent price changes for this shop
        from apps.inventory.models import ShopPrice
        context['recent_price_changes'] = ShopPrice.objects.filter(
            tenant=user.tenant,
            location=shop
        ).select_related('product').order_by('-created_at')[:10]
        
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
