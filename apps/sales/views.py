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
from django.utils.decorators import method_decorator
from django.db import transaction

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
        
        # Get products with prices for this shop
        products_with_prices = []
        for product in products:
            shop_price = product.shop_prices.filter(
                shop=user_shop, is_active=True
            ).first()
            
            if shop_price:
                products_with_prices.append({
                    'id': product.pk,
                    'name': product.name,
                    'sku': product.sku,
                    'category': product.category.name if product.category else 'Uncategorized',
                    'price': str(shop_price.selling_price),
                    'unit': product.unit_of_measure,
                })
        
        # Get categories for filtering
        from apps.inventory.models import Category
        categories = Category.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        )
        
        context = {
            'shop': user_shop,
            'shop_settings': shop_settings,
            'shift': open_shift,
            'products': json.dumps(products_with_prices),
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
        
        return render(request, self.template_name, {
            'shift': shift,
            'expected_cash': shift.expected_cash,
            'total_sales': shift.total_sales,
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
            shop=shop,
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
    
    shop = request.user.location
    
    if not shop or shop.location_type != 'SHOP':
        return JsonResponse({'error': 'No shop assigned'}, status=400)
    
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
            
            # Complete sale
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
