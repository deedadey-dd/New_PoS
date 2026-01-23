"""
Audit views for product tracking and profit/loss analysis.
Restricted to AUDITOR, ACCOUNTANT, and ADMIN roles.
"""
from django.views import View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Sum, Count, F, Q, Case, When, DecimalField
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from apps.inventory.models import Product, InventoryLedger
from apps.sales.models import Sale, SaleItem
from apps.core.models import Location, User
from apps.core.mixins import PaginationMixin


class AuditAccessMixin(PaginationMixin):
    """Mixin to restrict access to audit roles."""
    allowed_roles = ['AUDITOR', 'ACCOUNTANT', 'ADMIN']
    
    def dispatch(self, request, *args, **kwargs):
        if not request.user.role or request.user.role.name not in self.allowed_roles:
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def paginate_queryset(self, request, queryset):
        # This implementation is now in PaginationMixin.paginate_custom_queryset
        # But for compatibility with existing code during refactor we proxy it or just change calls
        return self.paginate_custom_queryset(queryset)


class ProductLifecycleView(LoginRequiredMixin, AuditAccessMixin, View):
    """
    Track a product's complete lifecycle from entry to exit.
    Shows: Entry points, distribution (transfers), and exits (sales, damage, etc.)
    """
    template_name = 'audit/product_lifecycle.html'
    
    def get(self, request, pk=None):
        tenant = request.user.tenant
        context = {
            'products': Product.objects.filter(tenant=tenant, is_active=True).order_by('name')
        }
        
        if pk:
            product = get_object_or_404(Product, pk=pk, tenant=tenant)
            context['selected_product'] = product
            
            # Get date range
            date_from = request.GET.get('date_from', '')
            date_to = request.GET.get('date_to', '')
            
            # Build filters
            filters = Q(tenant=tenant, product=product)
            if date_from:
                filters &= Q(created_at__date__gte=date_from)
                context['date_from'] = date_from
            if date_to:
                filters &= Q(created_at__date__lte=date_to)
                context['date_to'] = date_to
            
            ledger = InventoryLedger.objects.filter(filters).select_related(
                'location', 'batch', 'created_by'
            ).order_by('-created_at')
            
            # Group by transaction type for summary
            summary_data = ledger.values('transaction_type').annotate(
                total_qty=Sum('quantity'),
                entry_count=Count('id')
            )
            
            # Build summary dict
            summary = {}
            for item in summary_data:
                summary[item['transaction_type']] = {
                    'qty': item['total_qty'] or Decimal('0'),
                    'count': item['entry_count']
                }
            
            # Calculate running balance per location
            location_balances = ledger.values(
                'location__id', 'location__name', 'location__location_type'
            ).annotate(
                current_stock=Sum('quantity'),
            ).order_by('location__name')
            
            # Overall product stock
            total_stock = ledger.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            
            # Paginate ledger
            paginated_ledger, per_page = self.paginate_queryset(request, ledger)

            context.update({
                'ledger_entries': paginated_ledger,
                'page_obj': paginated_ledger,
                'per_page': per_page,
                'summary': summary,
                'location_balances': location_balances,
                'total_stock': total_stock,
            })
        
        return render(request, self.template_name, context)


class ProductProfitLossView(LoginRequiredMixin, AuditAccessMixin, View):
    """
    Profit/Loss analysis per product.
    Shows revenue, cost, gross profit, and margin for each product.
    """
    template_name = 'audit/product_profit_loss.html'
    
    def get(self, request):
        from datetime import datetime
        tenant = request.user.tenant
        
        # Parse date range - support both preset and custom
        date_range = request.GET.get('range', 'month')
        custom_from = request.GET.get('date_from')
        custom_to = request.GET.get('date_to')
        today = timezone.now().date()
        date_warning = None
        
        # Custom dates take priority
        if custom_from and custom_to:
            try:
                date_from = datetime.strptime(custom_from, '%Y-%m-%d').date()
                date_to = datetime.strptime(custom_to, '%Y-%m-%d').date()
                
                # Validate: from date should not be after to date
                if date_from > date_to:
                    date_warning = 'From date was after To date - dates have been swapped.'
                    date_from, date_to = date_to, date_from
                
                # Validate: dates should not be in the future
                if date_to > today:
                    date_warning = 'To date was in the future - adjusted to today.'
                    date_to = today
                
                # Validate: date range shouldn't be too large (over 2 years)
                if date_from and date_to and (date_to - date_from).days > 730:
                    date_warning = 'Date range exceeds 2 years. Consider using a shorter range for better performance.'
                
                date_label = f'{date_from.strftime("%b %d")} - {date_to.strftime("%b %d, %Y")}'
                date_range = 'custom'
            except ValueError:
                date_warning = 'Invalid date format. Using default 30-day range.'
                date_from = today - timedelta(days=30)
                date_to = today
                date_label = 'Last 30 Days'
        elif custom_from or custom_to:
            # Only one date provided
            date_warning = 'Both From and To dates are required for custom range. Using default.'
            date_to = today
            date_from = today - timedelta(days=30)
            date_label = 'Last 30 Days'
        else:
            date_to = today
            if date_range == 'week':
                date_from = today - timedelta(days=7)
                date_label = 'Last 7 Days'
            elif date_range == 'quarter':
                date_from = today - timedelta(days=90)
                date_label = 'Last 90 Days'
            elif date_range == 'year':
                date_from = today - timedelta(days=365)
                date_label = 'Last 365 Days'
            elif date_range == 'all':
                date_from = None
                date_to = None
                date_label = 'All Time'
            else:  # month (default)
                date_from = today - timedelta(days=30)
                date_label = 'Last 30 Days'
        
        # Build query
        filters = Q(sale__tenant=tenant, sale__status='COMPLETED')
        if date_from:
            filters &= Q(sale__created_at__date__gte=date_from)
        if date_to:
            filters &= Q(sale__created_at__date__lte=date_to)
        
        product_data = list(SaleItem.objects.filter(filters).values(
            'product__id', 'product__name', 'product__sku', 'product__category__name'
        ).annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total'),
            cost=Sum(F('quantity') * F('unit_cost')),
        ).order_by('-revenue'))
        
        # Calculate profit and margin
        for item in product_data:
            item['revenue'] = item['revenue'] or Decimal('0')
            item['cost'] = item['cost'] or Decimal('0')
            item['qty_sold'] = item['qty_sold'] or Decimal('0')
            item['profit'] = item['revenue'] - item['cost']
            if item['revenue'] > 0:
                item['margin_pct'] = round((item['profit'] / item['revenue']) * 100, 1)
            else:
                item['margin_pct'] = 0
        
        # Sort by profit
        product_data.sort(key=lambda x: x['profit'], reverse=True)
        
        # Totals
        totals = {
            'revenue': sum(p['revenue'] for p in product_data),
            'cost': sum(p['cost'] for p in product_data),
            'qty_sold': sum(p['qty_sold'] for p in product_data),
        }
        totals['profit'] = totals['revenue'] - totals['cost']
        if totals['revenue'] > 0:
            totals['margin'] = round((totals['profit'] / totals['revenue']) * 100, 1)
        else:
            totals['margin'] = 0
        
        # Paginate products
        paginated_products, per_page = self.paginate_queryset(request, product_data)

        context = {
            'products': paginated_products,
            'page_obj': paginated_products,
            'per_page': per_page,
            'totals': totals,
            'date_range': date_range,
            'date_label': date_label,
            'date_from': date_from,
            'date_to': date_to,
            'date_warning': date_warning,
        }
        
        return render(request, self.template_name, context)


class LocationProfitLossView(LoginRequiredMixin, AuditAccessMixin, View):
    """
    Profit/Loss analysis per shop/location.
    """
    template_name = 'audit/location_profit_loss.html'
    
    def get(self, request):
        from datetime import datetime
        tenant = request.user.tenant
        
        # Parse date range - support both preset and custom
        date_range = request.GET.get('range', 'month')
        custom_from = request.GET.get('date_from')
        custom_to = request.GET.get('date_to')
        today = timezone.now().date()
        date_warning = None
        
        # Custom dates take priority
        if custom_from and custom_to:
            try:
                date_from = datetime.strptime(custom_from, '%Y-%m-%d').date()
                date_to = datetime.strptime(custom_to, '%Y-%m-%d').date()
                
                # Validate: from date should not be after to date
                if date_from > date_to:
                    date_warning = 'From date was after To date - dates have been swapped.'
                    date_from, date_to = date_to, date_from
                
                # Validate: dates should not be in the future
                if date_to > today:
                    date_warning = 'To date was in the future - adjusted to today.'
                    date_to = today
                
                # Validate: date range shouldn't be too large (over 2 years)
                if date_from and date_to and (date_to - date_from).days > 730:
                    date_warning = 'Date range exceeds 2 years. Consider using a shorter range for better performance.'
                
                date_label = f'{date_from.strftime("%b %d")} - {date_to.strftime("%b %d, %Y")}'
                date_range = 'custom'
            except ValueError:
                date_warning = 'Invalid date format. Using default 30-day range.'
                date_from = today - timedelta(days=30)
                date_to = today
                date_label = 'Last 30 Days'
        elif custom_from or custom_to:
            # Only one date provided
            date_warning = 'Both From and To dates are required for custom range. Using default.'
            date_to = today
            date_from = today - timedelta(days=30)
            date_label = 'Last 30 Days'
        else:
            date_to = today
            if date_range == 'week':
                date_from = today - timedelta(days=7)
                date_label = 'Last 7 Days'
            elif date_range == 'quarter':
                date_from = today - timedelta(days=90)
                date_label = 'Last 90 Days'
            elif date_range == 'year':
                date_from = today - timedelta(days=365)
                date_label = 'Last 365 Days'
            elif date_range == 'all':
                date_from = None
                date_to = None
                date_label = 'All Time'
            else:
                date_from = today - timedelta(days=30)
                date_label = 'Last 30 Days'
        
        # Shop-level aggregation
        location_data = []
        shops = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True)
        
        for shop in shops:
            shop_sales = Sale.objects.filter(
                tenant=tenant, shop=shop, status='COMPLETED'
            )
            if date_from:
                shop_sales = shop_sales.filter(created_at__date__gte=date_from)
            if date_to:
                shop_sales = shop_sales.filter(created_at__date__lte=date_to)
            
            # Get items for these sales
            sale_items = SaleItem.objects.filter(sale__in=shop_sales)
            
            revenue = shop_sales.aggregate(total=Sum('total'))['total'] or Decimal('0')
            cost = sale_items.aggregate(
                total=Sum(F('quantity') * F('unit_cost'))
            )['total'] or Decimal('0')
            sale_count = shop_sales.count()
            
            profit = revenue - cost
            margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
            avg_sale = round(revenue / sale_count, 2) if sale_count > 0 else 0
            
            location_data.append({
                'shop': shop,
                'sale_count': sale_count,
                'revenue': revenue,
                'cost': cost,
                'profit': profit,
                'margin': margin,
                'avg_sale': avg_sale,
            })
        
        # Sort by profit
        location_data.sort(key=lambda x: x['profit'], reverse=True)
        
        # Totals
        totals = {
            'sale_count': sum(l['sale_count'] for l in location_data),
            'revenue': sum(l['revenue'] for l in location_data),
            'cost': sum(l['cost'] for l in location_data),
        }
        totals['profit'] = totals['revenue'] - totals['cost']
        totals['margin'] = round((totals['profit'] / totals['revenue'] * 100), 1) if totals['revenue'] > 0 else 0
        
        # Paginate locations
        paginated_locations, per_page = self.paginate_queryset(request, location_data)

        context = {
            'locations': paginated_locations,
            'page_obj': paginated_locations,
            'per_page': per_page,
            'totals': totals,
            'date_range': date_range,
            'date_label': date_label,
            'date_from': date_from,
            'date_to': date_to,
            'date_warning': date_warning,
        }
        
        return render(request, self.template_name, context)


class ManagerProfitLossView(LoginRequiredMixin, AuditAccessMixin, View):
    """
    Profit/Loss analysis per manager/attendant.
    """
    template_name = 'audit/manager_profit_loss.html'
    
    def get(self, request):
        from datetime import datetime
        tenant = request.user.tenant
        
        # Parse date range - support both preset and custom
        date_range = request.GET.get('range', 'month')
        custom_from = request.GET.get('date_from')
        custom_to = request.GET.get('date_to')
        today = timezone.now().date()
        date_warning = None
        
        # Custom dates take priority
        if custom_from and custom_to:
            try:
                date_from = datetime.strptime(custom_from, '%Y-%m-%d').date()
                date_to = datetime.strptime(custom_to, '%Y-%m-%d').date()
                
                # Validate: from date should not be after to date
                if date_from > date_to:
                    date_warning = 'From date was after To date - dates have been swapped.'
                    date_from, date_to = date_to, date_from
                
                # Validate: dates should not be in the future
                if date_to > today:
                    date_warning = 'To date was in the future - adjusted to today.'
                    date_to = today
                
                # Validate: date range shouldn't be too large (over 2 years)
                if date_from and date_to and (date_to - date_from).days > 730:
                    date_warning = 'Date range exceeds 2 years. Consider using a shorter range for better performance.'
                
                date_label = f'{date_from.strftime("%b %d")} - {date_to.strftime("%b %d, %Y")}'
                date_range = 'custom'
            except ValueError:
                date_warning = 'Invalid date format. Using default 30-day range.'
                date_from = today - timedelta(days=30)
                date_to = today
                date_label = 'Last 30 Days'
        elif custom_from or custom_to:
            # Only one date provided
            date_warning = 'Both From and To dates are required for custom range. Using default.'
            date_to = today
            date_from = today - timedelta(days=30)
            date_label = 'Last 30 Days'
        else:
            date_to = today
            if date_range == 'week':
                date_from = today - timedelta(days=7)
                date_label = 'Last 7 Days'
            elif date_range == 'quarter':
                date_from = today - timedelta(days=90)
                date_label = 'Last 90 Days'
            elif date_range == 'year':
                date_from = today - timedelta(days=365)
                date_label = 'Last 365 Days'
            elif date_range == 'all':
                date_from = None
                date_to = None
                date_label = 'All Time'
            else:
                date_from = today - timedelta(days=30)
                date_label = 'Last 30 Days'
        
        # Manager-level aggregation
        manager_data = []
        
        # Get all users who have made sales in the period
        base_sales_filter = Q(tenant=tenant, status='COMPLETED')
        if date_from:
            base_sales_filter &= Q(created_at__date__gte=date_from)
        if date_to:
            base_sales_filter &= Q(created_at__date__lte=date_to)
        
        attendants = User.objects.filter(tenant=tenant).filter(
            sales__in=Sale.objects.filter(base_sales_filter)
        ).distinct()
        
        for attendant in attendants:
            attendant_sales = Sale.objects.filter(base_sales_filter, attendant=attendant)
            
            sale_items = SaleItem.objects.filter(sale__in=attendant_sales)
            
            revenue = attendant_sales.aggregate(total=Sum('total'))['total'] or Decimal('0')
            cost = sale_items.aggregate(
                total=Sum(F('quantity') * F('unit_cost'))
            )['total'] or Decimal('0')
            sale_count = attendant_sales.count()
            
            if sale_count > 0:  # Only include if they have sales
                profit = revenue - cost
                margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
                avg_sale = round(revenue / sale_count, 2) if sale_count > 0 else 0
                
                manager_data.append({
                    'user': attendant,
                    'location': attendant.location,
                    'role': attendant.role,
                    'sale_count': sale_count,
                    'revenue': revenue,
                    'cost': cost,
                    'profit': profit,
                    'margin': margin,
                    'avg_sale': avg_sale,
                })
        
        manager_data.sort(key=lambda x: x['profit'], reverse=True)
        
        # Totals
        totals = {
            'sale_count': sum(m['sale_count'] for m in manager_data),
            'revenue': sum(m['revenue'] for m in manager_data),
            'cost': sum(m['cost'] for m in manager_data),
        }
        totals['profit'] = totals['revenue'] - totals['cost']
        totals['margin'] = round((totals['profit'] / totals['revenue'] * 100), 1) if totals['revenue'] > 0 else 0
        
        # Paginate managers
        paginated_managers, per_page = self.paginate_queryset(request, manager_data)

        context = {
            'managers': paginated_managers,
            'page_obj': paginated_managers,
            'per_page': per_page,
            'totals': totals,
            'date_range': date_range,
            'date_label': date_label,
            'date_from': date_from,
            'date_to': date_to,
            'date_warning': date_warning,
        }
        
        return render(request, self.template_name, context)


class InventoryMovementReportView(LoginRequiredMixin, AuditAccessMixin, View):
    """
    Comprehensive inventory movement report.
    Shows all stock movements with filtering by type, location, and date.
    """
    template_name = 'audit/inventory_movement_report.html'
    
    def get(self, request):
        tenant = request.user.tenant
        
        # Get filters
        transaction_type = request.GET.get('type', '')
        location_id = request.GET.get('location', '')
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        product_search = request.GET.get('product', '')
        
        # Build query
        filters = Q(tenant=tenant)
        
        if transaction_type:
            filters &= Q(transaction_type=transaction_type)
        if location_id:
            filters &= Q(location_id=location_id)
        if date_from:
            filters &= Q(created_at__date__gte=date_from)
        if date_to:
            filters &= Q(created_at__date__lte=date_to)
        if product_search:
            filters &= (Q(product__name__icontains=product_search) | 
                       Q(product__sku__icontains=product_search))
        
        ledger = InventoryLedger.objects.filter(filters).select_related(
            'product', 'location', 'batch', 'created_by'
        ).order_by('-created_at')

        paginated_ledger, per_page = self.paginate_queryset(request, ledger)
        
        # Summary by type
        summary_data = InventoryLedger.objects.filter(filters).values(
            'transaction_type'
        ).annotate(
            count=Count('id'),
            total_qty=Sum('quantity'),
        )
        
        summary = {}
        for item in summary_data:
            summary[item['transaction_type']] = {
                'count': item['count'],
                'qty': item['total_qty'] or Decimal('0'),
            }
        
        context = {
            'ledger': paginated_ledger,
            'page_obj': paginated_ledger,
            'per_page': per_page,
            'summary': summary,
            'locations': Location.objects.filter(tenant=tenant, is_active=True),
            'transaction_types': InventoryLedger.TRANSACTION_TYPES,
            'filters': {
                'type': transaction_type,
                'location': location_id,
                'date_from': date_from,
                'date_to': date_to,
                'product': product_search,
            }
        }
        
        return render(request, self.template_name, context)
