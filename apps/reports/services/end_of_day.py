"""
Business logic and service layer for End of Day Reports.
Provides high-integrity, read-only aggregation using Decimal arithmetic.
"""
from decimal import Decimal
from django.db.models import Sum, Count, Q
from django.utils import timezone

from apps.sales.models import Sale, SaleItem, Shift
from apps.inventory.models import InventoryLedger, Product
from apps.accounting.models import CashTransfer, BankTransfer, ExpenditureItem
from apps.customers.models import CustomerTransaction


def build_management_end_of_day_report(tenant, start_date, end_date, shop=None, user=None, exclude_inactive=True, top_n='all'):
    """
    Build the Management Overview End of Day Report according to executive specification.
    Returns a unified, serialized data structure for both HTML dashboard and PDF generation.
    """
    currency_symbol = tenant.currency if hasattr(tenant, 'currency') and tenant.currency else 'GHS'
    role_name = user.role.name if user and hasattr(user, 'role') and user.role else None

    # Base sales queryset
    sales = Sale.objects.filter(
        tenant=tenant,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
        status__in=['COMPLETED', 'PENDING_DISPATCH']
    )

    shop_filter_for_inventory = None
    if role_name == 'SHOP_ATTENDANT' and user:
        sales = sales.filter(attendant=user)
        shop_filter_for_inventory = user.location
    elif role_name == 'SHOP_CASHIER' and user:
        sales = sales.filter(cashier=user)
        shop_filter_for_inventory = user.location
    elif shop:
        sales = sales.filter(shop=shop)
        shop_filter_for_inventory = shop
    elif role_name == 'SHOP_MANAGER' and user and user.location:
        sales = sales.filter(shop=user.location)
        shop_filter_for_inventory = user.location

    # 1. SALES AGGREGATIONS
    sales_agg = sales.aggregate(
        total_rev=Sum('total'),
        inv_count=Count('id', distinct=True)
    )
    net_reported_sales = sales_agg['total_rev'] or Decimal('0.00')
    invoice_count = sales_agg['inv_count'] or 0

    items_sold_qty = SaleItem.objects.filter(sale__in=sales).aggregate(
        q=Sum('quantity')
    )['q'] or Decimal('0')

    avg_ticket = (net_reported_sales / Decimal(invoice_count)) if invoice_count > 0 else Decimal('0.00')
    items_per_invoice = (items_sold_qty / Decimal(invoice_count)) if invoice_count > 0 else Decimal('0.00')

    # Payment Breakdown
    cash_sales_full = sales.filter(payment_method='CASH').aggregate(t=Sum('total'))['t'] or Decimal('0.00')
    cash_sales_mixed = sales.filter(payment_method='MIXED').aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
    cash_sales = cash_sales_full + cash_sales_mixed

    ecash_sales = sales.filter(payment_method='ECASH').aggregate(t=Sum('total'))['t'] or Decimal('0.00')
    momo_sales = sales.filter(payment_method='LOCAL_MOMO').aggregate(t=Sum('total'))['t'] or Decimal('0.00')
    credit_sales = sales.filter(payment_method='CREDIT').aggregate(t=Sum('total'))['t'] or Decimal('0.00')

    cash_share_percent = ((cash_sales / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')

    # 2. FINANCIAL MOVEMENTS
    # Customer payments
    ct_filter = {
        'tenant': tenant,
        'transaction_type': 'CREDIT',
        'created_at__date__gte': start_date,
        'created_at__date__lte': end_date
    }
    if role_name in ['SHOP_ATTENDANT', 'SHOP_CASHIER'] and user:
        ct_filter['performed_by'] = user
    elif shop_filter_for_inventory:
        ct_filter['customer__shop'] = shop_filter_for_inventory

    customer_payments = CustomerTransaction.objects.filter(**ct_filter).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    # Funds to accountant
    acc_transfer_filter = {
        'tenant': tenant,
        'status': 'CONFIRMED',
        'transfer_type': 'DEPOSIT',
        'confirmed_at__date__gte': start_date,
        'confirmed_at__date__lte': end_date,
    }
    if shop_filter_for_inventory:
        acc_transfer_filter['from_location'] = shop_filter_for_inventory
    elif role_name in ['SHOP_ATTENDANT', 'SHOP_CASHIER'] and user:
        acc_transfer_filter['from_user'] = user

    funds_to_accountant = CashTransfer.objects.filter(
        **acc_transfer_filter
    ).filter(
        Q(destination='ACCOUNTANT') | Q(destination__isnull=True) | ~Q(destination='BANK')
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR'] and not shop_filter_for_inventory:
        funds_to_accountant = CashTransfer.objects.filter(
            tenant=tenant,
            status='CONFIRMED',
            transfer_type='DEPOSIT',
            confirmed_at__date__gte=start_date,
            confirmed_at__date__lte=end_date
        ).filter(
            Q(destination='ACCOUNTANT') | Q(destination__isnull=True) | ~Q(destination='BANK')
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    # Funds sent to bank
    bank_ct_filter = {
        'tenant': tenant,
        'status': 'CONFIRMED',
        'transfer_type': 'DEPOSIT',
        'destination': 'BANK',
        'confirmed_at__date__gte': start_date,
        'confirmed_at__date__lte': end_date,
    }
    if shop_filter_for_inventory:
        bank_ct_filter['from_location'] = shop_filter_for_inventory
    elif role_name in ['SHOP_ATTENDANT', 'SHOP_CASHIER'] and user:
        bank_ct_filter['from_user'] = user

    cash_to_bank = CashTransfer.objects.filter(**bank_ct_filter).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    bt_filter = {
        'tenant': tenant,
        'created_at__date__gte': start_date,
        'created_at__date__lte': end_date
    }
    if shop_filter_for_inventory:
        bt_filter['accountant__location'] = shop_filter_for_inventory
    elif role_name in ['SHOP_CASHIER', 'ACCOUNTANT'] and user:
        bt_filter['accountant'] = user

    bank_transfers = BankTransfer.objects.filter(**bt_filter).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    funds_to_bank = cash_to_bank + bank_transfers

    # Expenditures
    exp_filter = {
        'request__tenant': tenant,
        'status': 'APPROVED',
        'approved_at__date__gte': start_date,
        'approved_at__date__lte': end_date
    }
    if shop_filter_for_inventory:
        exp_filter['request__location'] = shop_filter_for_inventory

    expenditures = ExpenditureItem.objects.filter(**exp_filter).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

    # 3. PRODUCT BREAKDOWN & DISPATCH
    items_agg = SaleItem.objects.filter(sale__in=sales).values(
        'product_id', 'product__name', 'product__category__name'
    ).annotate(
        total_qty=Sum('quantity'),
        total_revenue=Sum('total')
    ).order_by('-total_qty', 'product__name')

    product_stats = {}
    for item in items_agg:
        product_stats[item['product_id']] = {
            'product_id': item['product_id'],
            'name': item['product__name'],
            'category_name': item['product__category__name'] or '',
            'qty_sold': item['total_qty'] or Decimal('0'),
            'revenue': item['total_revenue'] or Decimal('0.00'),
            'qty_dispatched': None,  # None indicates not recorded / dash
            'stock_left': 'N/A'
        }

    is_strict_workflow = bool(getattr(tenant, 'use_strict_sales_workflow', False))

    if is_strict_workflow:
        # Dispatch tracking from InventoryLedger (Strict workflow only)
        dispatch_filter = {
            'tenant': tenant,
            'transaction_type': 'SALE',
            'created_at__date__gte': start_date,
            'created_at__date__lte': end_date
        }
        return_filter = {
            'tenant': tenant,
            'transaction_type': 'SALE_RETURN',
            'created_at__date__gte': start_date,
            'created_at__date__lte': end_date
        }
        if role_name == 'SHOP_ATTENDANT' and user:
            dispatch_filter['created_by'] = user
            return_filter['created_by'] = user
        elif shop_filter_for_inventory:
            dispatch_filter['location'] = shop_filter_for_inventory
            return_filter['location'] = shop_filter_for_inventory

        returned_map = {}
        for row in InventoryLedger.objects.filter(**return_filter).values('product_id').annotate(ret_qty=Sum('quantity')):
            returned_map[row['product_id']] = abs(row['ret_qty'] or Decimal('0'))

        dispatched_agg = InventoryLedger.objects.filter(**dispatch_filter).values(
            'product_id', 'product__name', 'product__category__name'
        ).annotate(dispatched_qty=Sum('quantity'))

        for row in dispatched_agg:
            pid = row['product_id']
            dq = max(abs(row['dispatched_qty'] or Decimal('0')) - returned_map.get(pid, Decimal('0')), Decimal('0'))
            if pid in product_stats:
                product_stats[pid]['qty_dispatched'] = dq
            elif not exclude_inactive:
                product_stats[pid] = {
                    'product_id': pid,
                    'name': row['product__name'],
                    'category_name': row['product__category__name'] or '',
                    'qty_sold': Decimal('0'),
                    'revenue': Decimal('0.00'),
                    'qty_dispatched': dq,
                    'stock_left': 'N/A'
                }

    # Stock Left calculation from ledger
    stock_filter = {'tenant': tenant, 'product_id__in': product_stats.keys()}
    if shop_filter_for_inventory:
        stock_filter['location'] = shop_filter_for_inventory

    if product_stats:
        ledger_agg = InventoryLedger.objects.filter(**stock_filter).values('product_id').annotate(stock=Sum('quantity'))
        for row in ledger_agg:
            product_stats[row['product_id']]['stock_left'] = row['stock'] or Decimal('0')

    # Ordered list of products (sorted by quantity sold desc)
    sorted_products = sorted(product_stats.values(), key=lambda x: x['qty_sold'], reverse=True)

    # 4. RECONCILIATION REVIEW (Three-state: RECONCILED, VARIANCE, INSUFFICIENT_DATA)
    # Check if shift records exist for closing cash count
    shift_qs = Shift.objects.filter(
        tenant=tenant,
        start_time__date__gte=start_date,
        start_time__date__lte=end_date
    )
    if shop_filter_for_inventory:
        shift_qs = shift_qs.filter(shop=shop_filter_for_inventory)

    shifts_list = list(shift_qs)
    has_full_shift_audit = False
    counted_closing_cash = None
    opening_float = None

    if shifts_list and all(s.closing_cash is not None for s in shifts_list):
        has_full_shift_audit = True
        opening_float = sum((s.opening_cash for s in shifts_list), Decimal('0.00'))
        counted_closing_cash = sum((s.closing_cash for s in shifts_list), Decimal('0.00'))

    reconciliation_status = 'INSUFFICIENT_DATA'
    reconciliation_badge_class = 'badge-warning text-dark'
    reconciliation_label = 'INSUFFICIENT DATA'
    bank_over_sales_diff = Decimal('0.00')

    if funds_to_bank > net_reported_sales:
        bank_over_sales_diff = funds_to_bank - net_reported_sales

    if not has_full_shift_audit:
        reconciliation_status = 'INSUFFICIENT_DATA'
        reconciliation_label = 'INSUFFICIENT DATA'
        if bank_over_sales_diff > Decimal('0.00'):
            reconciliation_text = (
                f"Bank transfer exceeds today's sales by {currency_symbol} {bank_over_sales_diff:,.2f}. "
                "Opening cash, prior-period collections, transfers and closing cash count are not provided; "
                "variance cannot be established."
            )
        else:
            reconciliation_text = (
                "Opening cash, prior-period collections, transfers and closing cash count are not provided; "
                "variance cannot be established."
            )
    else:
        # Full cash calculations
        expected_cash = (
            opening_float + cash_sales + customer_payments - expenditures - funds_to_bank - funds_to_accountant
        )
        variance = counted_closing_cash - expected_cash
        if variance == Decimal('0.00'):
            reconciliation_status = 'RECONCILED'
            reconciliation_label = 'RECONCILED'
            reconciliation_badge_class = 'badge-success text-white'
            reconciliation_text = "All cash collections, bank transfers, and counted till closing balances match expected ledger totals."
        else:
            reconciliation_status = 'VARIANCE'
            reconciliation_label = 'VARIANCE DETECTED'
            reconciliation_badge_class = 'badge-danger text-white'
            reconciliation_text = f"Counted till cash deviates from expected cash by {currency_symbol} {abs(variance):,.2f} ({'Overage' if variance > 0 else 'Shortage'})."

    # 5. DAILY CONTROL CHECKS
    product_rows_count = len(sorted_products)
    product_revenue_sum = sum((p['revenue'] for p in sorted_products), Decimal('0.00'))
    product_qty_sum = sum((p['qty_sold'] for p in sorted_products), Decimal('0'))

    sales_product_match = (product_revenue_sum == net_reported_sales and product_qty_sum == items_sold_qty)

    # 6. MERCHANDISING / PRODUCT PERFORMANCE
    # Top revenue SKU
    sorted_by_revenue = sorted(sorted_products, key=lambda x: x['revenue'], reverse=True)
    top_revenue_sku = None
    if sorted_by_revenue and sorted_by_revenue[0]['revenue'] > Decimal('0'):
        top_sku = sorted_by_revenue[0]
        share = ((top_sku['revenue'] / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')
        top_revenue_sku = {
            'name': top_sku['name'],
            'revenue': top_sku['revenue'],
            'units': top_sku['qty_sold'],
            'share': share
        }

    # Top volume item
    sorted_by_qty = sorted(sorted_products, key=lambda x: x['qty_sold'], reverse=True)
    top_volume_item = None
    if sorted_by_qty and sorted_by_qty[0]['qty_sold'] > Decimal('0'):
        top_vol = sorted_by_qty[0]
        vol_share = ((top_vol['qty_sold'] / items_sold_qty) * Decimal('100.0')) if items_sold_qty > 0 else Decimal('0.0')
        rev_share = ((top_vol['revenue'] / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')
        top_volume_item = {
            'name': top_vol['name'],
            'units': top_vol['qty_sold'],
            'revenue': top_vol['revenue'],
            'vol_share': vol_share,
            'rev_share': rev_share
        }

    # Category / Board Revenue Grouping
    # Look for 'Board' in product name or category, or fall back to dominant category
    board_products = [
        p for p in sorted_products if 'board' in p['name'].lower() or 'board' in p.get('category_name', '').lower()
    ]
    if board_products:
        board_revenue = sum((p['revenue'] for p in board_products), Decimal('0.00'))
        board_share = ((board_revenue / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')
        category_highlight = {
            'label': 'BOARD REVENUE',
            'revenue': board_revenue,
            'share': board_share
        }
    else:
        # Group by category
        cat_groups = {}
        for p in sorted_products:
            cname = p.get('category_name') or 'General'
            cat_groups[cname] = cat_groups.get(cname, Decimal('0.00')) + p['revenue']
        if cat_groups:
            top_cat_name, top_cat_rev = max(cat_groups.items(), key=lambda x: x[1])
            cshare = ((top_cat_rev / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')
            category_highlight = {
                'label': f"{top_cat_name.upper()} REVENUE",
                'revenue': top_cat_rev,
                'share': cshare
            }
        else:
            category_highlight = {
                'label': 'TOP CATEGORY',
                'revenue': net_reported_sales,
                'share': Decimal('100.0')
            }

    # Revenue leaders (top 7)
    revenue_leaders = []
    top_sku_rev = sorted_by_revenue[0]['revenue'] if sorted_by_revenue else Decimal('1.00')
    for p in sorted_by_revenue[:7]:
        if p['revenue'] <= Decimal('0'):
            continue
        pshare = ((p['revenue'] / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')
        bwidth = min(Decimal('100.0'), max(Decimal('4.0'), ((p['revenue'] / top_sku_rev) * Decimal('100.0')))) if top_sku_rev > 0 else Decimal('0.0')
        rem_bwidth = max(Decimal('0.0'), Decimal('100.0') - bwidth)
        revenue_leaders.append({
            'name': p['name'],
            'revenue': p['revenue'],
            'share': pshare,
            'bar_width': bwidth,
            'rem_bar_width': rem_bwidth
        })

    # Dynamic Management Reading Synthesis
    if top_revenue_sku and top_volume_item:
        management_reading = (
            f"{top_revenue_sku['name']} contributes {top_revenue_sku['share']:.1f}% of sales from only {top_revenue_sku['units']:.0f} units. "
            f"{top_volume_item['name']} contributes {top_volume_item['vol_share']:.1f}% of unit volume but just {top_volume_item['rev_share']:.1f}% of revenue."
        )
    else:
        management_reading = "Sales and product distributions are in initial recording state."

    # 7. OPERATIONS: INVENTORY & DISPATCH
    if is_strict_workflow:
        dispatch_numeric_rows = [p for p in sorted_products if p.get('qty_dispatched') is not None]
        dispatch_reported_sum = sum((p['qty_dispatched'] for p in dispatch_numeric_rows), Decimal('0'))
        dispatch_numeric_count = len(dispatch_numeric_rows)
    else:
        dispatch_reported_sum = Decimal('0')
        dispatch_numeric_count = 0

    # Stock Watchlist (screening indicator for stock <= 25)
    stock_numeric_rows = [p for p in sorted_products if isinstance(p.get('stock_left'), (int, float, Decimal))]
    sorted_by_stock = sorted(stock_numeric_rows, key=lambda x: x['stock_left'])
    stock_watch_items = [p for p in sorted_by_stock if p['stock_left'] <= Decimal('25')]
    stock_watch_count = len(stock_watch_items)

    total_stock_units = sum((p['stock_left'] for p in stock_numeric_rows if p['stock_left'] > Decimal('0')), Decimal('0'))
    in_stock_count = sum(1 for p in stock_numeric_rows if p['stock_left'] > Decimal('0'))

    watchlist_display = []
    for p in sorted_by_stock[:6]:
        if is_strict_workflow:
            disp_txt = f"{p['qty_dispatched']:.0f}" if p.get('qty_dispatched') is not None else "not recorded"
        else:
            disp_txt = None
        watchlist_display.append({
            'name': p['name'],
            'stock_left': p['stock_left'],
            'sold_qty': p['qty_sold'],
            'dispatched_txt': disp_txt,
            'is_low': p['stock_left'] <= Decimal('25')
        })

    # 8. PAYMENT MIX SUMMARY LIST
    payments_list = []
    if cash_sales > Decimal('0.00'):
        pct = ((cash_sales / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('100.0')
        payments_list.append({
            'name': 'Cash',
            'amount': cash_sales,
            'percentage': pct,
            'rem_percentage': max(Decimal('0.0'), Decimal('100.0') - pct),
            'color': '#0d9488'
        })
    if ecash_sales > Decimal('0.00'):
        pct = ((ecash_sales / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')
        payments_list.append({
            'name': 'E-Cash',
            'amount': ecash_sales,
            'percentage': pct,
            'rem_percentage': max(Decimal('0.0'), Decimal('100.0') - pct),
            'color': '#2563eb'
        })
    if momo_sales > Decimal('0.00'):
        pct = ((momo_sales / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')
        payments_list.append({
            'name': 'Local MoMo',
            'amount': momo_sales,
            'percentage': pct,
            'rem_percentage': max(Decimal('0.0'), Decimal('100.0') - pct),
            'color': '#f59e0b'
        })
    if credit_sales > Decimal('0.00'):
        pct = ((credit_sales / net_reported_sales) * Decimal('100.0')) if net_reported_sales > 0 else Decimal('0.0')
        payments_list.append({
            'name': 'Credit',
            'amount': credit_sales,
            'percentage': pct,
            'rem_percentage': max(Decimal('0.0'), Decimal('100.0') - pct),
            'color': '#9333ea'
        })

    if not payments_list:
        payments_list.append({
            'name': 'Cash',
            'amount': Decimal('0.00'),
            'percentage': Decimal('100.0'),
            'rem_percentage': Decimal('0.0'),
            'color': '#0d9488'
        })

    # Non-cash note
    zero_methods = []
    if ecash_sales == Decimal('0.00'):
        zero_methods.append('E-cash')
    if momo_sales == Decimal('0.00'):
        zero_methods.append('Local MoMo')
    if credit_sales == Decimal('0.00'):
        zero_methods.append('credit sales')
    if customer_payments == Decimal('0.00'):
        zero_methods.append('customer payments')

    if len(zero_methods) == 4:
        non_cash_notes = "E-cash, Local MoMo, credit sales and customer payments all reported as 0.00."
    elif zero_methods:
        non_cash_notes = f"{', '.join(zero_methods)} reported as 0.00."
    else:
        non_cash_notes = "All payment tenders recorded with positive transaction totals."

    # Date displays
    date_display = start_date.strftime('%d %B %Y').upper() if start_date == end_date else f"{start_date.strftime('%d %b %Y').upper()} - {end_date.strftime('%d %b %Y').upper()}"

    # Appendix rows formatting
    appendix_rows = []
    for i, p in enumerate(sorted_products, 1):
        appendix_rows.append({
            'rank': i,
            'name': p['name'],
            'qty_sold': p['qty_sold'],
            'revenue': p['revenue'],
            'qty_dispatched': p['qty_dispatched'],
            'stock_left': p['stock_left']
        })

    return {
        'tenant': tenant,
        'company_name': tenant.name.upper(),
        'shop': shop,
        'shop_name': shop.name if shop else ('All Shops' if not shop_filter_for_inventory else shop_filter_for_inventory.name),
        'start_date': start_date,
        'end_date': end_date,
        'date_display': date_display,
        'currency_symbol': currency_symbol,
        'role_name': role_name,
        
        # Section 1: Sales & Controls
        'net_reported_sales': net_reported_sales,
        'cash_share_percent': cash_share_percent,
        'invoice_count': invoice_count,
        'avg_ticket': avg_ticket,
        'units_sold': items_sold_qty,
        'items_per_invoice': items_per_invoice,
        
        # Payment Mix & Financial Movements
        'payments_list': payments_list,
        'non_cash_notes': non_cash_notes,
        'sales_cash': cash_sales,
        'customer_payments': customer_payments,
        'expenses': expenditures,
        'funds_to_bank': funds_to_bank,
        'funds_to_accountant': funds_to_accountant,
        
        # Reconciliation Review
        'reconciliation_status': reconciliation_status,
        'reconciliation_label': reconciliation_label,
        'reconciliation_badge_class': reconciliation_badge_class,
        'reconciliation_text': reconciliation_text,
        'bank_over_sales_diff': bank_over_sales_diff,
        
        # Daily Control Checks
        'product_rows_count': product_rows_count,
        'product_revenue_sum': product_revenue_sum,
        'product_qty_sum': product_qty_sum,
        'sales_product_match': sales_product_match,
        
        # Section 2: Merchandising / Product Performance
        'top_revenue_sku': top_revenue_sku,
        'top_volume_item': top_volume_item,
        'category_highlight': category_highlight,
        'revenue_leaders': revenue_leaders,
        'management_reading': management_reading,
        
        # Section 3: Operations / Inventory & Dispatch
        'use_strict_sales_workflow': is_strict_workflow,
        'dispatch_reported_sum': dispatch_reported_sum,
        'dispatch_numeric_count': dispatch_numeric_count,
        'stock_watch_count': stock_watch_count,
        'total_stock_units': total_stock_units,
        'in_stock_count': in_stock_count,
        'watchlist_display': watchlist_display,
        
        # Section 4: Appendix
        'appendix_rows': appendix_rows,
        'verified_total_sold': items_sold_qty,
        'verified_total_revenue': net_reported_sales,
        
        # Raw sorted products
        'products': sorted_products,
    }
