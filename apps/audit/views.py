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
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.role or request.user.role.name not in self.allowed_roles:
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)

    def paginate_queryset(self, request, queryset):
        # This implementation is now in PaginationMixin.paginate_custom_queryset
        # But for compatibility with existing code during refactor we proxy it or just change calls
        return self.paginate_custom_queryset(queryset)


from apps.core.mixins import SortableMixin

class ProductLifecycleView(LoginRequiredMixin, AuditAccessMixin, SortableMixin, View):
    """
    Track a product's complete lifecycle from entry to exit.
    Shows: Entry points, distribution (transfers), and exits (sales, damage, etc.)
    """
    template_name = 'audit/product_lifecycle.html'
    sortable_fields = ['created_at', 'transaction_type', 'quantity', 'location__name', 'batch__batch_number']
    default_sort = '-created_at'
    
    def get(self, request, pk=None):
        tenant = request.user.tenant
        context = {
            'products': Product.objects.filter(tenant=tenant, is_active=True).order_by('name')
        }
        
        if pk:
            product = get_object_or_404(Product, pk=pk, tenant=tenant)
            context['selected_product'] = product
            
            # Get date range
            date_from = request.GET.get('date_from', '').strip()
            date_to = request.GET.get('date_to', '').strip()
            
            # Validate: swap if date_from > date_to
            if date_from and date_to and date_from > date_to:
                date_from, date_to = date_to, date_from
                messages.info(request, 'Start date was after end date; dates have been swapped.')
            
            # Base query for all time
            all_time_ledger = InventoryLedger.objects.filter(
                tenant=tenant, product=product
            )
            
            # Period filters
            period_filters = Q(tenant=tenant, product=product)
            has_date_filter = False
            if date_from:
                period_filters &= Q(created_at__date__gte=date_from)
                context['date_from'] = date_from
                has_date_filter = True
            if date_to:
                period_filters &= Q(created_at__date__lte=date_to)
                context['date_to'] = date_to
                has_date_filter = True
            
            ledger = InventoryLedger.objects.filter(period_filters).select_related(
                'location', 'batch', 'created_by'
            )
            ledger = self.apply_sorting(ledger)
            
            # ── 1. Stock Entries (Inflows) Breakdown ──────────────────
            in_entries = ledger.filter(transaction_type='IN', quantity__gt=0)
            
            bulk_filter = Q(reference_type='BulkBatch') | Q(notes__icontains='bulk receive')
            bulk_receive_qty = in_entries.filter(bulk_filter).aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            bulk_receive_count = in_entries.filter(bulk_filter).count()

            init_filter = (
                Q(reference_type__startswith='DEMO-SEED') | 
                Q(reference_type__in=['Initial', 'INITIAL']) | 
                Q(notes__icontains='initial demo')
            ) & ~bulk_filter & ~Q(reference_type__in=['Batch', 'GoodsReceipt'])
            initial_stock_qty = in_entries.filter(init_filter).aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            initial_stock_count = in_entries.filter(init_filter).count()

            batch_filter = ~bulk_filter & ~init_filter
            batch_receive_qty = in_entries.filter(batch_filter).aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            batch_receive_count = in_entries.filter(batch_filter).count()

            total_in_qty = bulk_receive_qty + initial_stock_qty + batch_receive_qty
            total_in_count = in_entries.count()

            prod_entries = ledger.filter(transaction_type='PRODUCTION', quantity__gt=0)
            production_qty = prod_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            production_count = prod_entries.count()

            transfer_in_entries = ledger.filter(transaction_type='TRANSFER_IN', quantity__gt=0)
            transfer_in_qty = transfer_in_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            transfer_in_count = transfer_in_entries.count()

            returns_entries = ledger.filter(
                transaction_type__in=['RETURN', 'SALE_RETURN', 'SALE_VOID'],
                quantity__gt=0
            )
            returns_qty = returns_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            returns_count = returns_entries.count()

            adjust_pos_entries = ledger.filter(transaction_type='ADJUST', quantity__gt=0)
            adjust_pos_qty = adjust_pos_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            adjust_pos_count = adjust_pos_entries.count()

            dispute_entries = ledger.filter(transaction_type='DISPUTE_REVERSAL', quantity__gt=0)
            dispute_qty = dispute_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            dispute_count = dispute_entries.count()

            known_pos_types = [
                'IN', 'PRODUCTION', 'TRANSFER_IN', 'RETURN', 
                'SALE_RETURN', 'SALE_VOID', 'ADJUST', 'DISPUTE_REVERSAL'
            ]
            other_pos_entries = ledger.exclude(transaction_type__in=known_pos_types).filter(quantity__gt=0)
            other_pos_qty = other_pos_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            other_pos_count = other_pos_entries.count()

            total_entries_qty = (
                total_in_qty + production_qty + transfer_in_qty + 
                returns_qty + adjust_pos_qty + dispute_qty + other_pos_qty
            )
            total_entries_count = (
                total_in_count + production_count + transfer_in_count +
                returns_count + adjust_pos_count + dispute_count + other_pos_count
            )

            # ── 2. Stock Exits (Outflows) Breakdown ───────────────────
            sales_entries = ledger.filter(transaction_type='SALE', quantity__lt=0)
            sales_qty = sales_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            sales_count = sales_entries.count()

            transfer_out_entries = ledger.filter(transaction_type='TRANSFER_OUT', quantity__lt=0)
            transfer_out_qty = transfer_out_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            transfer_out_count = transfer_out_entries.count()

            damage_entries = ledger.filter(transaction_type='DAMAGE', quantity__lt=0)
            damage_qty = damage_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            damage_count = damage_entries.count()

            adjust_neg_entries = ledger.filter(transaction_type='ADJUST', quantity__lt=0)
            adjust_neg_qty = adjust_neg_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            adjust_neg_count = adjust_neg_entries.count()

            stock_out_entries = ledger.filter(transaction_type='OUT', quantity__lt=0)
            stock_out_qty = stock_out_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            stock_out_count = stock_out_entries.count()

            known_neg_types = ['SALE', 'TRANSFER_OUT', 'DAMAGE', 'ADJUST', 'OUT']
            other_neg_entries = ledger.exclude(transaction_type__in=known_neg_types).filter(quantity__lt=0)
            other_neg_qty = other_neg_entries.aggregate(q=Sum('quantity'))['q'] or Decimal('0')
            other_neg_count = other_neg_entries.count()

            total_exits_qty = (
                sales_qty + transfer_out_qty + damage_qty + 
                adjust_neg_qty + stock_out_qty + other_neg_qty
            )
            total_exits_count = (
                sales_count + transfer_out_count + damage_count +
                adjust_neg_count + stock_out_count + other_neg_count
            )

            # Period Net Movement
            period_net_movement = total_entries_qty + total_exits_qty

            # ── 3. Strict Sales Workflow: Pending Dispatches (Committed Stock) ──
            pending_sales_items = SaleItem.objects.filter(
                sale__tenant=tenant,
                product=product,
                sale__status__in=['PENDING_DISPATCH', 'COMPLETED'],
                dispatched_quantity__lt=F('quantity')
            ).exclude(sale__status__in=['VOIDED', 'REFUNDED']).select_related('sale', 'sale__shop', 'sale__customer')

            total_pending_dispatch_qty = pending_sales_items.aggregate(
                pending=Sum(F('quantity') - F('dispatched_quantity'))
            )['pending'] or Decimal('0')
            pending_dispatch_count = pending_sales_items.count()

            loc_pending_map = {
                item['sale__shop_id']: item['pending']
                for item in pending_sales_items.order_by().values('sale__shop_id').annotate(
                    pending=Sum(F('quantity') - F('dispatched_quantity'))
                )
            }

            # ── 4. Stock by Location & Overall Total Stock ────────────
            # True current total physical stock of the product across all locations (all-time)
            current_total_stock = all_time_ledger.aggregate(total=Sum('quantity'))['total'] or Decimal('0')
            available_total_stock = current_total_stock - total_pending_dispatch_qty
            total_sales_committed = abs(sales_qty) + total_pending_dispatch_qty

            # Locations: Map all-time stock and period movements
            all_time_loc_map = {
                item['location_id']: item['qty']
                for item in all_time_ledger.order_by().values('location_id').annotate(qty=Sum('quantity'))
            }
            
            period_loc_map = {}
            if has_date_filter:
                period_loc_map = {
                    item['location_id']: item['qty']
                    for item in ledger.order_by().values('location_id').annotate(qty=Sum('quantity'))
                }

            tenant_locations = Location.objects.filter(tenant=tenant, is_active=True).order_by('name')
            location_balances = []
            for loc in tenant_locations:
                loc_stock = all_time_loc_map.get(loc.pk, Decimal('0'))
                period_movement = period_loc_map.get(loc.pk, Decimal('0'))
                loc_pending = loc_pending_map.get(loc.pk, Decimal('0'))
                loc_avail = loc_stock - loc_pending
                
                # Show location if it has stock, historical movements, period activity, or pending dispatch
                if loc_stock != Decimal('0') or period_movement != Decimal('0') or loc_pending != Decimal('0') or loc.pk in all_time_loc_map:
                    location_balances.append({
                        'location__id': loc.pk,
                        'location__name': loc.name,
                        'location__location_type': loc.location_type,
                        'current_stock': loc_stock,
                        'period_movement': period_movement,
                        'pending_dispatch': loc_pending,
                        'available_stock': loc_avail,
                    })

            # Include inactive locations if they still hold or held stock
            known_loc_ids = set(loc['location__id'] for loc in location_balances)
            missing_loc_ids = set(all_time_loc_map.keys()) - known_loc_ids
            if missing_loc_ids:
                missing_locs = Location.objects.filter(pk__in=missing_loc_ids)
                for loc in missing_locs:
                    loc_stock = all_time_loc_map.get(loc.pk, Decimal('0'))
                    loc_pending = loc_pending_map.get(loc.pk, Decimal('0'))
                    location_balances.append({
                        'location__id': loc.pk,
                        'location__name': loc.name + (' (Inactive)' if not loc.is_active else ''),
                        'location__location_type': loc.location_type,
                        'current_stock': loc_stock,
                        'period_movement': period_loc_map.get(loc.pk, Decimal('0')),
                        'pending_dispatch': loc_pending,
                        'available_stock': loc_stock - loc_pending,
                    })
            location_balances.sort(key=lambda x: x['location__name'])

            # Legacy summary dictionary for backward compatibility
            summary_data = ledger.order_by().values('transaction_type').annotate(
                total_qty=Sum('quantity'),
                entry_count=Count('id')
            )
            summary = {}
            for item in summary_data:
                summary[item['transaction_type']] = {
                    'qty': item['total_qty'] or Decimal('0'),
                    'count': item['entry_count']
                }

            # Paginate ledger
            paginated_ledger, per_page = self.paginate_queryset(request, ledger)

            has_strict_workflow = getattr(tenant, 'use_strict_sales_workflow', False) or (total_pending_dispatch_qty > 0)

            context.update({
                'ledger_entries': paginated_ledger,
                'page_obj': paginated_ledger,
                'per_page': per_page,
                'summary': summary,
                'location_balances': location_balances,
                'current_total_stock': current_total_stock,
                'total_stock': current_total_stock,
                'available_total_stock': available_total_stock,
                'total_pending_dispatch_qty': total_pending_dispatch_qty,
                'total_sales_committed': total_sales_committed,
                'pending_dispatch_count': pending_dispatch_count,
                'pending_sales_items': pending_sales_items[:50],
                'has_strict_workflow': has_strict_workflow,
                'period_net_movement': period_net_movement,
                'has_date_filter': has_date_filter,
                
                # Entries
                'bulk_receive_qty': bulk_receive_qty,
                'bulk_receive_count': bulk_receive_count,
                'batch_receive_qty': batch_receive_qty,
                'batch_receive_count': batch_receive_count,
                'initial_stock_qty': initial_stock_qty,
                'initial_stock_count': initial_stock_count,
                'total_in_qty': total_in_qty,
                'total_in_count': total_in_count,
                'production_qty': production_qty,
                'production_count': production_count,
                'transfer_in_qty': transfer_in_qty,
                'transfer_in_count': transfer_in_count,
                'returns_qty': returns_qty,
                'returns_count': returns_count,
                'adjust_pos_qty': adjust_pos_qty,
                'adjust_pos_count': adjust_pos_count,
                'dispute_qty': dispute_qty,
                'dispute_count': dispute_count,
                'other_pos_qty': other_pos_qty,
                'other_pos_count': other_pos_count,
                'total_entries_qty': total_entries_qty,
                'total_entries_count': total_entries_count,
                
                # Exits
                'sales_qty': sales_qty,
                'sales_count': sales_count,
                'transfer_out_qty': transfer_out_qty,
                'transfer_out_count': transfer_out_count,
                'damage_qty': damage_qty,
                'damage_count': damage_count,
                'adjust_neg_qty': adjust_neg_qty,
                'adjust_neg_count': adjust_neg_count,
                'stock_out_qty': stock_out_qty,
                'stock_out_count': stock_out_count,
                'other_neg_qty': other_neg_qty,
                'other_neg_count': other_neg_count,
                'total_exits_qty': total_exits_qty,
                'total_exits_count': total_exits_count,
                
                'current_sort': self.request.GET.get('sort', ''),
                'current_dir': self.request.GET.get('dir', 'asc'),
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
        filters = Q(sale__tenant=tenant, sale__status__in=['COMPLETED', 'PENDING_DISPATCH'])
        if date_from:
            filters &= Q(sale__created_at__date__gte=date_from)
        if date_to:
            filters &= Q(sale__created_at__date__lte=date_to)
            
        location_id = request.GET.get('location')
        if location_id:
            filters &= Q(sale__shop_id=location_id)
            
        q = request.GET.get('q')
        if q:
            filters &= (Q(product__name__icontains=q) | Q(product__sku__icontains=q))
            
        category_id = request.GET.get('category')
        if category_id:
            filters &= Q(product__category_id=category_id)
        
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
                
        # Sort logic
        sort_by = request.GET.get('sort', 'profit')
        sort_dir = request.GET.get('dir', 'desc')
        valid_sort_fields = ['product__name', 'qty_sold', 'revenue', 'cost', 'profit', 'margin_pct']
        if sort_by not in valid_sort_fields:
            sort_by = 'profit'
            
        reverse_sort = sort_dir == 'desc'
        if sort_by == 'product__name':
            product_data.sort(key=lambda x: x['product__name'].lower(), reverse=reverse_sort)
        else:
            product_data.sort(key=lambda x: x[sort_by], reverse=reverse_sort)
        
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

        from apps.core.models import Location
        from apps.inventory.models import Category

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
            'current_sort': sort_by,
            'current_dir': sort_dir,
            'locations': Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True),
            'categories': Category.objects.filter(tenant=tenant, is_active=True),
            'selected_location': int(location_id) if location_id and location_id.isdigit() else None,
            'search_q': q,
            'selected_category': int(category_id) if category_id and category_id.isdigit() else None,
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
        
        q = request.GET.get('q')
        category_id = request.GET.get('category')
        
        for shop in shops:
            shop_sales = Sale.objects.filter(
                tenant=tenant, shop=shop, status__in=['COMPLETED', 'PENDING_DISPATCH']
            )
            if date_from:
                shop_sales = shop_sales.filter(created_at__date__gte=date_from)
            if date_to:
                shop_sales = shop_sales.filter(created_at__date__lte=date_to)
            
            # Get items for these sales
            sale_items = SaleItem.objects.filter(sale__in=shop_sales)
            
            item_filters = Q()
            if q:
                item_filters &= (Q(product__name__icontains=q) | Q(product__sku__icontains=q))
            if category_id:
                item_filters &= Q(product__category_id=category_id)
                
            if item_filters:
                sale_items = sale_items.filter(item_filters)
                revenue = sale_items.aggregate(total=Sum('total'))['total'] or Decimal('0')
                sale_count = shop_sales.filter(items__in=sale_items).distinct().count()
            else:
                revenue = shop_sales.aggregate(total=Sum('total'))['total'] or Decimal('0')
                sale_count = shop_sales.count()
            
            cost = sale_items.aggregate(
                total=Sum(F('quantity') * F('unit_cost'))
            )['total'] or Decimal('0')
            
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
        
        # Sorting logic
        sort_by = request.GET.get('sort', 'profit')
        sort_dir = request.GET.get('dir', 'desc')
        valid_sort_fields = ['shop', 'sale_count', 'revenue', 'cost', 'profit', 'margin', 'avg_sale']
        if sort_by not in valid_sort_fields:
            sort_by = 'profit'
            
        reverse_sort = sort_dir == 'desc'
        if sort_by == 'shop':
            location_data.sort(key=lambda x: x['shop'].name.lower(), reverse=reverse_sort)
        else:
            location_data.sort(key=lambda x: x[sort_by], reverse=reverse_sort)
        
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

        from apps.inventory.models import Category

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
            'current_sort': sort_by,
            'current_dir': sort_dir,
            'categories': Category.objects.filter(tenant=tenant, is_active=True),
            'search_q': q,
            'selected_category': int(category_id) if category_id and category_id.isdigit() else None,
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
        
        location_id = request.GET.get('location')
        q = request.GET.get('q')
        category_id = request.GET.get('category')
        
        # Get all users who have made sales in the period
        base_sales_filter = Q(tenant=tenant, status__in=['COMPLETED', 'PENDING_DISPATCH'])
        if date_from:
            base_sales_filter &= Q(created_at__date__gte=date_from)
        if date_to:
            base_sales_filter &= Q(created_at__date__lte=date_to)
        if location_id:
            base_sales_filter &= Q(shop_id=location_id)
        
        attendants = User.objects.filter(tenant=tenant).filter(
            sales__in=Sale.objects.filter(base_sales_filter)
        ).distinct()
        
        for attendant in attendants:
            attendant_sales = Sale.objects.filter(base_sales_filter, attendant=attendant)
            
            sale_items = SaleItem.objects.filter(sale__in=attendant_sales)
            
            item_filters = Q()
            if q:
                item_filters &= (Q(product__name__icontains=q) | Q(product__sku__icontains=q))
            if category_id:
                item_filters &= Q(product__category_id=category_id)
                
            if item_filters:
                sale_items = sale_items.filter(item_filters)
                revenue = sale_items.aggregate(total=Sum('total'))['total'] or Decimal('0')
                sale_count = attendant_sales.filter(items__in=sale_items).distinct().count()
            else:
                revenue = attendant_sales.aggregate(total=Sum('total'))['total'] or Decimal('0')
                sale_count = attendant_sales.count()
            
            cost = sale_items.aggregate(
                total=Sum(F('quantity') * F('unit_cost'))
            )['total'] or Decimal('0')
            
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
        
        # Sort logic
        sort_by = request.GET.get('sort', 'profit')
        sort_dir = request.GET.get('dir', 'desc')
        valid_sort_fields = ['user', 'sale_count', 'revenue', 'cost', 'profit', 'margin', 'avg_sale']
        if sort_by not in valid_sort_fields:
            sort_by = 'profit'
            
        reverse_sort = sort_dir == 'desc'
        if sort_by == 'user':
            manager_data.sort(key=lambda x: x['user'].get_full_name().lower() if x['user'].get_full_name() else '', reverse=reverse_sort)
        else:
            manager_data.sort(key=lambda x: x[sort_by], reverse=reverse_sort)
        
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

        from apps.core.models import Location
        from apps.inventory.models import Category

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
            'current_sort': sort_by,
            'current_dir': sort_dir,
            'locations': Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True),
            'categories': Category.objects.filter(tenant=tenant, is_active=True),
            'selected_location': int(location_id) if location_id and location_id.isdigit() else None,
            'search_q': q,
            'selected_category': int(category_id) if category_id and category_id.isdigit() else None,
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


class ProductProfitLossExportView(LoginRequiredMixin, AuditAccessMixin, View):
    """Export product P&L to Excel."""

    def get(self, request):
        from datetime import datetime
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        tenant = request.user.tenant

        # Parse date range (same as ProductProfitLossView)
        date_range = request.GET.get('range', 'month')
        custom_from = request.GET.get('date_from')
        custom_to = request.GET.get('date_to')
        today = timezone.now().date()

        if custom_from and custom_to:
            try:
                date_from = datetime.strptime(custom_from, '%Y-%m-%d').date()
                date_to = datetime.strptime(custom_to, '%Y-%m-%d').date()
                if date_from > date_to:
                    date_from, date_to = date_to, date_from
                if date_to > today:
                    date_to = today
            except ValueError:
                date_from = today - timedelta(days=30)
                date_to = today
        else:
            date_to = today
            if date_range == 'week':
                date_from = today - timedelta(days=7)
            elif date_range == 'quarter':
                date_from = today - timedelta(days=90)
            elif date_range == 'year':
                date_from = today - timedelta(days=365)
            elif date_range == 'all':
                date_from = None
                date_to = None
            else:
                date_from = today - timedelta(days=30)

        filters = Q(sale__tenant=tenant, sale__status__in=['COMPLETED', 'PENDING_DISPATCH'])
        if date_from:
            filters &= Q(sale__created_at__date__gte=date_from)
        if date_to:
            filters &= Q(sale__created_at__date__lte=date_to)

        location_id = request.GET.get('location')
        if location_id:
            filters &= Q(sale__shop_id=location_id)
            
        q = request.GET.get('q')
        if q:
            filters &= (Q(product__name__icontains=q) | Q(product__sku__icontains=q))
            
        category_id = request.GET.get('category')
        if category_id:
            filters &= Q(product__category_id=category_id)

        product_data = list(SaleItem.objects.filter(filters).values(
            'product__name', 'product__sku', 'product__category__name'
        ).annotate(
            qty_sold=Sum('quantity'),
            revenue=Sum('total'),
            cost=Sum(F('quantity') * F('unit_cost')),
        ).order_by('-revenue'))

        headers = ['Product', 'SKU', 'Category', 'Qty Sold', 'Revenue', 'Cost', 'Profit', 'Margin %']
        rows = []
        for item in product_data:
            revenue = item['revenue'] or Decimal('0')
            cost = item['cost'] or Decimal('0')
            profit = revenue - cost
            margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
            rows.append([
                item['product__name'],
                item['product__sku'] or '',
                item['product__category__name'] or '',
                float(item['qty_sold'] or 0),
                float(revenue),
                float(cost),
                float(profit),
                margin,
            ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            date_range_str = f"{date_from} to {date_to}" if date_from and date_to else "All Time"
            metadata = {
                'generator_name': request.user.get_full_name() or request.user.email,
                'shop_name': 'All Shops',
                'date_range': date_range_str
            }
            return export_to_pdf('product_profit_loss.pdf', 'Product Profit & Loss', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Product Profit & Loss', headers, rows)
            return build_excel_response(wb, 'product_profit_loss_export.xlsx')


class LocationProfitLossExportView(LoginRequiredMixin, AuditAccessMixin, View):
    """Export location P&L to Excel."""

    def get(self, request):
        from datetime import datetime
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        tenant = request.user.tenant

        # Parse date range (same logic as LocationProfitLossView)
        date_range = request.GET.get('range', 'month')
        custom_from = request.GET.get('date_from')
        custom_to = request.GET.get('date_to')
        today = timezone.now().date()

        if custom_from and custom_to:
            try:
                date_from = datetime.strptime(custom_from, '%Y-%m-%d').date()
                date_to = datetime.strptime(custom_to, '%Y-%m-%d').date()
                if date_from > date_to:
                    date_from, date_to = date_to, date_from
                if date_to > today:
                    date_to = today
            except ValueError:
                date_from = today - timedelta(days=30)
                date_to = today
        else:
            date_to = today
            if date_range == 'week':
                date_from = today - timedelta(days=7)
            elif date_range == 'quarter':
                date_from = today - timedelta(days=90)
            elif date_range == 'year':
                date_from = today - timedelta(days=365)
            elif date_range == 'all':
                date_from = None
                date_to = None
            else:
                date_from = today - timedelta(days=30)

        shops = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True)

        headers = ['Shop', 'Sales Count', 'Revenue', 'Cost', 'Profit', 'Margin %', 'Avg Sale']
        rows = []

        q = request.GET.get('q')
        category_id = request.GET.get('category')

        for shop in shops:
            shop_sales = Sale.objects.filter(tenant=tenant, shop=shop, status__in=['COMPLETED', 'PENDING_DISPATCH'])
            if date_from:
                shop_sales = shop_sales.filter(created_at__date__gte=date_from)
            if date_to:
                shop_sales = shop_sales.filter(created_at__date__lte=date_to)

            sale_items = SaleItem.objects.filter(sale__in=shop_sales)
            
            item_filters = Q()
            if q:
                item_filters &= (Q(product__name__icontains=q) | Q(product__sku__icontains=q))
            if category_id:
                item_filters &= Q(product__category_id=category_id)
                
            if item_filters:
                sale_items = sale_items.filter(item_filters)
                revenue = sale_items.aggregate(total=Sum('total'))['total'] or Decimal('0')
                sale_count = shop_sales.filter(items__in=sale_items).distinct().count()
            else:
                revenue = shop_sales.aggregate(total=Sum('total'))['total'] or Decimal('0')
                sale_count = shop_sales.count()
            
            cost = sale_items.aggregate(
                total=Sum(F('quantity') * F('unit_cost'))
            )['total'] or Decimal('0')

            profit = revenue - cost
            margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
            avg_sale = round(revenue / sale_count, 2) if sale_count > 0 else 0

            rows.append([
                shop.name,
                sale_count,
                float(revenue),
                float(cost),
                float(profit),
                margin,
                float(avg_sale),
            ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            date_range_str = f"{date_from} to {date_to}" if date_from and date_to else "All Time"
            metadata = {
                'generator_name': request.user.get_full_name() or request.user.email,
                'shop_name': 'All Shops',
                'date_range': date_range_str
            }
            return export_to_pdf('location_profit_loss.pdf', 'Location Profit & Loss', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Location Profit & Loss', headers, rows)
            return build_excel_response(wb, 'location_profit_loss_export.xlsx')


class ManagerProfitLossExportView(LoginRequiredMixin, AuditAccessMixin, View):
    """Export manager P&L to Excel."""

    def get(self, request):
        from datetime import datetime
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        tenant = request.user.tenant

        # Parse date range
        date_range = request.GET.get('range', 'month')
        custom_from = request.GET.get('date_from')
        custom_to = request.GET.get('date_to')
        today = timezone.now().date()

        if custom_from and custom_to:
            try:
                date_from = datetime.strptime(custom_from, '%Y-%m-%d').date()
                date_to = datetime.strptime(custom_to, '%Y-%m-%d').date()
                if date_from > date_to:
                    date_from, date_to = date_to, date_from
                if date_to > today:
                    date_to = today
            except ValueError:
                date_from = today - timedelta(days=30)
                date_to = today
        else:
            date_to = today
            if date_range == 'week':
                date_from = today - timedelta(days=7)
            elif date_range == 'quarter':
                date_from = today - timedelta(days=90)
            elif date_range == 'year':
                date_from = today - timedelta(days=365)
            elif date_range == 'all':
                date_from = None
                date_to = None
            else:
                date_from = today - timedelta(days=30)

        location_id = request.GET.get('location')
        q = request.GET.get('q')
        category_id = request.GET.get('category')

        base_sales_filter = Q(tenant=tenant, status__in=['COMPLETED', 'PENDING_DISPATCH'])
        if date_from:
            base_sales_filter &= Q(created_at__date__gte=date_from)
        if date_to:
            base_sales_filter &= Q(created_at__date__lte=date_to)
        if location_id:
            base_sales_filter &= Q(shop_id=location_id)

        attendants = User.objects.filter(tenant=tenant).filter(
            sales__in=Sale.objects.filter(base_sales_filter)
        ).distinct()

        headers = ['Name', 'Email', 'Location', 'Role', 'Sales Count', 'Revenue', 'Cost', 'Profit', 'Margin %', 'Avg Sale']
        rows = []

        for attendant in attendants:
            attendant_sales = Sale.objects.filter(base_sales_filter, attendant=attendant)
            sale_items = SaleItem.objects.filter(sale__in=attendant_sales)

            item_filters = Q()
            if q:
                item_filters &= (Q(product__name__icontains=q) | Q(product__sku__icontains=q))
            if category_id:
                item_filters &= Q(product__category_id=category_id)
                
            if item_filters:
                sale_items = sale_items.filter(item_filters)
                revenue = sale_items.aggregate(total=Sum('total'))['total'] or Decimal('0')
                sale_count = attendant_sales.filter(items__in=sale_items).distinct().count()
            else:
                revenue = attendant_sales.aggregate(total=Sum('total'))['total'] or Decimal('0')
                sale_count = attendant_sales.count()

            cost = sale_items.aggregate(
                total=Sum(F('quantity') * F('unit_cost'))
            )['total'] or Decimal('0')

            if sale_count > 0:
                profit = revenue - cost
                margin = round((profit / revenue * 100), 1) if revenue > 0 else 0
                avg_sale = round(revenue / sale_count, 2) if sale_count > 0 else 0

                rows.append([
                    attendant.get_full_name() or attendant.email,
                    attendant.email,
                    attendant.location.name if attendant.location else '',
                    attendant.role.name if attendant.role else '',
                    sale_count,
                    float(revenue),
                    float(cost),
                    float(profit),
                    margin,
                    float(avg_sale),
                ])

        export_format = request.GET.get('format', 'excel')
        if export_format == 'pdf':
            from apps.core.pdf_utils import export_to_pdf
            date_range_str = f"{date_from} to {date_to}" if date_from and date_to else "All Time"
            metadata = {
                'generator_name': request.user.get_full_name() or request.user.email,
                'shop_name': 'All Shops',
                'date_range': date_range_str
            }
            return export_to_pdf('manager_profit_loss.pdf', 'Manager Profit & Loss', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Manager Profit & Loss', headers, rows)
            return build_excel_response(wb, 'manager_profit_loss_export.xlsx')


class InventoryMovementExportView(LoginRequiredMixin, AuditAccessMixin, View):
    """Export inventory movements to Excel."""

    def get(self, request):
        from apps.core.excel_utils import create_export_workbook, build_excel_response

        tenant = request.user.tenant

        # Get filters (same as InventoryMovementReportView)
        transaction_type = request.GET.get('type', '')
        location_id = request.GET.get('location', '')
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        product_search = request.GET.get('product', '')

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

        headers = ['Date', 'Product', 'SKU', 'Location', 'Type', 'Quantity',
                    'Unit Cost', 'Batch', 'User', 'Notes']
        rows = []
        for entry in ledger:
            rows.append([
                entry.created_at.strftime('%Y-%m-%d %H:%M') if entry.created_at else '',
                entry.product.name if entry.product else '',
                entry.product.sku if entry.product else '',
                entry.location.name if entry.location else '',
                entry.get_transaction_type_display() if hasattr(entry, 'get_transaction_type_display') else entry.transaction_type,
                float(entry.quantity) if entry.quantity else 0,
                float(entry.unit_cost) if entry.unit_cost else 0,
                entry.batch.batch_number if entry.batch else '',
                entry.created_by.get_full_name() or entry.created_by.email if entry.created_by else '',
                entry.notes or '',
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
                
            shop_name = "All Locations"
            if location_id:
                from apps.core.models import Location
                loc = Location.objects.filter(pk=location_id).first()
                if loc: shop_name = loc.name
                
            metadata = {
                'generator_name': request.user.get_full_name() or request.user.email,
                'shop_name': shop_name,
                'date_range': date_range_str
            }
            return export_to_pdf('inventory_movements.pdf', 'Inventory Movements', headers, rows, metadata=metadata)
        else:
            wb = create_export_workbook('Inventory Movements', headers, rows)
            return build_excel_response(wb, 'inventory_movements_export.xlsx')

class UserActivityListView(LoginRequiredMixin, AuditAccessMixin, View):
    """
    Log of users where auditors and admin can see when users are logged in and what they do.
    """
    template_name = 'audit/user_activity_log.html'
    
    def get(self, request):
        from .models import UserActivity
        tenant = request.user.tenant
        
        # Get filters
        action = request.GET.get('action', '')
        user_id = request.GET.get('user', '')
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        
        filters = Q(tenant=tenant)
        if action:
            filters &= Q(action=action)
        if user_id:
            filters &= Q(user_id=user_id)
        if date_from:
            filters &= Q(timestamp__date__gte=date_from)
        if date_to:
            filters &= Q(timestamp__date__lte=date_to)
            
        activities = UserActivity.objects.filter(filters).select_related('user').order_by('-timestamp')
        
        paginated_activities, per_page = self.paginate_queryset(request, activities)
        
        users = User.objects.filter(tenant=tenant, is_active=True).order_by('first_name')
        
        context = {
            'activities': paginated_activities,
            'page_obj': paginated_activities,
            'per_page': per_page,
            'users': users,
            'action_choices': UserActivity.ACTION_CHOICES,
            'filters': {
                'action': action,
                'user': user_id,
                'date_from': date_from,
                'date_to': date_to,
            }
        }
        
        return render(request, self.template_name, context)
