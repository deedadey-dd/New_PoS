from django.shortcuts import render
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.db.models import Sum, Count, Q
from decimal import Decimal
from datetime import datetime

from apps.sales.models import Sale, SaleItem
from apps.customers.models import CustomerTransaction
from apps.accounting.models import CashTransfer, BankTransfer, ExpenditureItem
from apps.payments.models import ECashLedger

class EndOfDaySummaryView(LoginRequiredMixin, View):
    """
    Role-specific end of day summary report.
    Shows the daily activity and financial summary for the requested date.
    """
    template_name = 'reports/end_of_day_summary.html'

    def get(self, request):
        user = request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None

        # Determine target dates
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        
        today = timezone.now().date()
        
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today
        except ValueError:
            start_date = today
            
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today
        except ValueError:
            end_date = today

        # Ensure start_date is before or equal to end_date
        if start_date > end_date:
            start_date = end_date

        context = {
            'start_date': start_date,
            'end_date': end_date,
            'role_name': role_name,
            'role_display_name': role_name.replace('_', ' ').title() if role_name else 'Unknown',
        }

        # Shared base filters
        date_filter = {
            'created_at__date__gte': start_date,
            'created_at__date__lte': end_date,
            'tenant': tenant
        }

        # If Accountant/Admin is viewing a specific shop
        viewing_shop_id = request.GET.get('shop_id')
        viewing_shop = None
        
        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            from apps.core.models import Location
            context['available_shops'] = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True)
            
            if viewing_shop_id:
                try:
                    viewing_shop = Location.objects.get(id=viewing_shop_id, tenant=tenant, location_type='SHOP')
                    context['viewing_shop'] = viewing_shop
                except Location.DoesNotExist:
                    pass

            summary_context = {}
            # ---------------------------------------------------------
            # ATTENDANT SUMMARY
            # ---------------------------------------------------------
            if role_name == 'SHOP_ATTENDANT':
                sales = Sale.objects.filter(**date_filter, attendant=user, status__in=['COMPLETED', 'PENDING_DISPATCH'])
                summary_context['total_sales_value'] = sales.aggregate(t=Sum('total'))['t'] or Decimal('0.00')
                summary_context['invoice_count'] = sales.count()
                
                summary_context['items_sold_qty'] = SaleItem.objects.filter(
                    sale__in=sales
                ).aggregate(q=Sum('quantity'))['q'] or Decimal('0')
    
                summary_context['customer_payments_received'] = CustomerTransaction.objects.filter(
                    **date_filter,
                    performed_by=user,
                    transaction_type='CREDIT'
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
            # ---------------------------------------------------------
            # CASHIER SUMMARY
            # ---------------------------------------------------------
            elif role_name == 'SHOP_CASHIER':
                sales = Sale.objects.filter(**date_filter, cashier=user, status__in=['COMPLETED', 'PENDING_DISPATCH'])
                summary_context['invoice_count'] = sales.count()
                
                methods = sales.values('payment_method').annotate(total=Sum('amount_paid'))
                payments_breakdown = {m['payment_method']: m['total'] for m in methods}
                summary_context['payments_breakdown'] = payments_breakdown
                summary_context['total_sales_value'] = sum(payments_breakdown.values()) or Decimal('0.00')
    
                summary_context['customer_payments_received'] = CustomerTransaction.objects.filter(
                    **date_filter,
                    performed_by=user,
                    transaction_type='CREDIT'
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
                summary_context['bank_transfers_made'] = BankTransfer.objects.filter(
                    **date_filter,
                    accountant=user
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
            # ---------------------------------------------------------
            # SHOP MANAGER OR ACCOUNTANT VIEWING SHOP SUMMARY
            # ---------------------------------------------------------
            elif role_name == 'SHOP_MANAGER' or viewing_shop:
                shop = viewing_shop if viewing_shop else user.location
                if shop:
                    sales = Sale.objects.filter(**date_filter, shop=shop, status__in=['COMPLETED', 'PENDING_DISPATCH'])
                    summary_context['shop_total_sales_value'] = sales.aggregate(t=Sum('total'))['t'] or Decimal('0.00')
                    summary_context['shop_invoice_count'] = sales.count()
                    
                    summary_context['shop_items_sold_qty'] = SaleItem.objects.filter(
                        sale__in=sales
                    ).aggregate(q=Sum('quantity'))['q'] or Decimal('0')
    
                    summary_context['shop_customer_payments_received'] = CustomerTransaction.objects.filter(
                        **date_filter,
                        customer__shop=shop,
                        transaction_type='CREDIT'
                    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
                    methods = sales.values('payment_method').annotate(total=Sum('amount_paid'))
                    payments_breakdown = {m['payment_method']: m['total'] for m in methods if m['total'] and m['total'] > 0}
                    
                    # Debt generated (unpaid amounts from sales)
                    total_billed = summary_context['shop_total_sales_value']
                    total_paid = sales.aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
                    credit_sales_generated = total_billed - total_paid
                    if credit_sales_generated > 0:
                        payments_breakdown['UNPAID (CREDIT SALES)'] = credit_sales_generated
    
                    summary_context['payments_breakdown'] = payments_breakdown
    
                    summary_context['transferred_funds_out'] = CashTransfer.objects.filter(
                        tenant=tenant,
                        from_location=shop,
                        status='CONFIRMED',
                        transfer_type='DEPOSIT',
                        confirmed_at__date__gte=start_date,
                        confirmed_at__date__lte=end_date
                    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
                    summary_context['shop_expenditures'] = ExpenditureItem.objects.filter(
                        request__tenant=tenant,
                        request__location=shop,
                        status='APPROVED',
                        approved_at__date__gte=start_date,
                        approved_at__date__lte=end_date
                    ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
                    
                    # Internal funds received - skip this if Accountant viewing the shop, or if using strict workflow
                    if role_name == 'SHOP_MANAGER' and not tenant.use_strict_sales_workflow:
                        summary_context['transferred_funds_in'] = CashTransfer.objects.filter(
                            tenant=tenant,
                            to_user=user,
                            status='CONFIRMED',
                            confirmed_at__date__gte=start_date,
                            confirmed_at__date__lte=end_date
                        ).exclude(from_user=user).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
                    
                    # Bank transfers made by cashiers in this shop (only in strict workflow)
                    if tenant.use_strict_sales_workflow:
                        summary_context['shop_bank_transfers_made'] = BankTransfer.objects.filter(
                            tenant=tenant,
                            accountant__location=shop,
                            created_at__date__gte=start_date,
                            created_at__date__lte=end_date
                        ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
            # ---------------------------------------------------------
            # OVERALL VIEW FOR ACCOUNTANT, ADMIN, AUDITOR
            # ---------------------------------------------------------
            elif role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
                sales = Sale.objects.filter(**date_filter, status__in=['COMPLETED', 'PENDING_DISPATCH'])
                methods = sales.values('payment_method').annotate(total=Sum('amount_paid'))
                payments_breakdown = {m['payment_method']: m['total'] for m in methods if m['total'] and m['total'] > 0}
                
                total_billed = sales.aggregate(t=Sum('total'))['t'] or Decimal('0.00')
                total_paid = sales.aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
                credit_sales_generated = total_billed - total_paid
                if credit_sales_generated > 0:
                    payments_breakdown['UNPAID (CREDIT SALES)'] = credit_sales_generated
    
                summary_context['payments_breakdown'] = payments_breakdown
    
                summary_context['cash_transfers_received'] = CashTransfer.objects.filter(
                    tenant=tenant,
                    to_user=user,
                    status='CONFIRMED',
                    transfer_type='DEPOSIT',
                    confirmed_at__date__gte=start_date,
                    confirmed_at__date__lte=end_date
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
                # Bank transfers made by THIS user only
                summary_context['bank_transfers_made'] = BankTransfer.objects.filter(
                    **date_filter,
                    accountant=user
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
                summary_context['expenditures_disbursed'] = ExpenditureItem.objects.filter(
                    request__tenant=tenant,
                    status='APPROVED',
                    approved_at__date__gte=start_date,
                    approved_at__date__lte=end_date,
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
    
                summary_context['digital_funds_withdrawn'] = ECashLedger.objects.filter(
                    **date_filter,
                    transaction_type='WITHDRAWAL',
                    created_by=user
                ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
                
                if summary_context['digital_funds_withdrawn'] < 0:
                    summary_context['digital_funds_withdrawn'] = abs(summary_context['digital_funds_withdrawn'])
                    
            context.update(summary_context)
        if request.GET.get('pdf') == 'true':
            from django.template.loader import get_template
            from django.http import HttpResponse
            from xhtml2pdf import pisa
            from io import BytesIO
            
            template = get_template('reports/pdf_summary_template.html')
            html = template.render(context)
            result = BytesIO()
            pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
            if not pdf.err:
                response = HttpResponse(result.getvalue(), content_type='application/pdf')
                response['Content-Disposition'] = f'attachment; filename="end_of_day_summary_{start_date}.pdf"'
                return response

        return render(request, self.template_name, context)

from apps.inventory.models import InventoryLedger

class EndOfDayDetailsView(LoginRequiredMixin, View):
    template_name = 'reports/end_of_day_details.html'

    def get(self, request):
        user = request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None

        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        
        today = timezone.now().date()
        
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today
        except ValueError:
            start_date = today
            
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today
        except ValueError:
            end_date = today

        if start_date > end_date:
            start_date = end_date
            
        exclude_inactive = request.GET.get('exclude_inactive', 'true').lower() != 'false'
        top_n_str = request.GET.get('top_n', 'all')
        
        viewing_shop_id = request.GET.get('shop_id')
        viewing_shop = None
        
        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            from apps.core.models import Location
            if viewing_shop_id:
                try:
                    viewing_shop = Location.objects.get(id=viewing_shop_id, tenant=tenant, location_type='SHOP')
                except Location.DoesNotExist:
                    pass

        sales = Sale.objects.filter(
            tenant=tenant,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date,
            status__in=['COMPLETED', 'PENDING_DISPATCH']
        )
        
        shop_filter_for_inventory = None

        if role_name == 'SHOP_ATTENDANT':
            sales = sales.filter(attendant=user)
            shop_filter_for_inventory = user.location
        elif role_name == 'SHOP_CASHIER':
            sales = sales.filter(cashier=user)
            shop_filter_for_inventory = user.location
        elif role_name == 'SHOP_MANAGER' or viewing_shop:
            shop = viewing_shop if viewing_shop else user.location
            sales = sales.filter(shop=shop)
            shop_filter_for_inventory = shop

        items_agg = SaleItem.objects.filter(sale__in=sales).values(
            'product_id', 'product__name'
        ).annotate(
            total_qty=Sum('quantity'),
            total_revenue=Sum('total')
        ).order_by('-total_qty')
        
        product_stats = {}
        for item in items_agg:
            product_stats[item['product_id']] = {
                'name': item['product__name'],
                'qty_sold': item['total_qty'],
                'revenue': item['total_revenue'],
                'qty_dispatched': Decimal('0'),
                'stock_left': 'N/A'
            }
            
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
        if role_name == 'SHOP_ATTENDANT':
            dispatch_filter['created_by'] = user
            return_filter['created_by'] = user
        elif shop_filter_for_inventory:
            dispatch_filter['location'] = shop_filter_for_inventory
            return_filter['location'] = shop_filter_for_inventory

        # Build a map of product_id → returned qty to subtract from dispatched
        from django.db.models import Q as _Q
        returned_map = {}
        for row in InventoryLedger.objects.filter(**return_filter).values('product_id').annotate(ret_qty=Sum('quantity')):
            returned_map[row['product_id']] = abs(row['ret_qty'] or Decimal('0'))

        dispatched_agg = InventoryLedger.objects.filter(**dispatch_filter).values('product_id', 'product__name').annotate(
            dispatched_qty=Sum('quantity')
        )
        for row in dispatched_agg:
            pid = row['product_id']
            # Net dispatched = dispatched_out - returned (floor at 0)
            dq = max(abs(row['dispatched_qty'] or Decimal('0')) - returned_map.get(pid, Decimal('0')), Decimal('0'))
            if pid in product_stats:
                product_stats[pid]['qty_dispatched'] = dq
            elif not exclude_inactive:
                product_stats[pid] = {
                    'name': row['product__name'],
                    'qty_sold': Decimal('0'),
                    'revenue': Decimal('0.00'),
                    'qty_dispatched': dq,
                    'stock_left': 'N/A'
                }
            
        if shop_filter_for_inventory:
            if not exclude_inactive:
                # Pull all products at this location from the ledger
                ledger_agg = InventoryLedger.objects.filter(
                    tenant=tenant, location=shop_filter_for_inventory
                ).values('product_id', 'product__name').annotate(
                    stock=Sum('quantity')
                )
                for row in ledger_agg:
                    pid = row['product_id']
                    stock = row['stock'] or Decimal('0')
                    if pid not in product_stats:
                        product_stats[pid] = {
                            'name': row['product__name'],
                            'qty_sold': Decimal('0'),
                            'revenue': Decimal('0.00'),
                            'qty_dispatched': Decimal('0'),
                            'stock_left': stock
                        }
                    else:
                        product_stats[pid]['stock_left'] = stock
            else:
                # Only look up stock for products that had sales or dispatches
                if product_stats:
                    ledger_agg = InventoryLedger.objects.filter(
                        tenant=tenant,
                        location=shop_filter_for_inventory,
                        product_id__in=product_stats.keys()
                    ).values('product_id').annotate(stock=Sum('quantity'))
                    for row in ledger_agg:
                        product_stats[row['product_id']]['stock_left'] = row['stock'] or Decimal('0')
                    
        sorted_stats = sorted(product_stats.values(), key=lambda x: x['qty_sold'], reverse=True)
        
        others_qty = Decimal('0')
        others_rev = Decimal('0.00')
        others_disp = Decimal('0')
        
        if top_n_str in ['10', '20']:
            n = int(top_n_str)
            if len(sorted_stats) > n:
                top_items = sorted_stats[:n]
                others = sorted_stats[n:]
                for o in others:
                    others_qty += o['qty_sold']
                    others_rev += o['revenue']
                    others_disp += o.get('qty_dispatched', Decimal('0'))
                    
                if others_qty > 0 or others_rev > 0 or others_disp > 0:
                    top_items.append({
                        'name': 'Others',
                        'qty_sold': others_qty,
                        'revenue': others_rev,
                        'qty_dispatched': others_disp,
                        'stock_left': '-'
                    })
                sorted_stats = top_items

        # --- 10 SUMMARY METRICS CALCULATION ---
        # 1. Total Sales
        shop_total_sales_value = sales.aggregate(t=Sum('total'))['t'] or Decimal('0.00')
        
        # 2. Invoices & Items Sold
        invoice_count = sales.count()
        items_sold_qty = SaleItem.objects.filter(sale__in=sales).aggregate(q=Sum('quantity'))['q'] or Decimal('0')
        
        # 3. Dispatched items (for strict workflow)
        summary_dispatched_raw = InventoryLedger.objects.filter(**dispatch_filter).aggregate(q=Sum('quantity'))['q'] or Decimal('0')
        summary_returned_raw = InventoryLedger.objects.filter(**return_filter).aggregate(q=Sum('quantity'))['q'] or Decimal('0')
        items_dispatched_qty = max(abs(summary_dispatched_raw) - abs(summary_returned_raw), Decimal('0'))
        
        # 4. Payment breakdown: Cash, E-Cash, Local Momo, Credit
        # Full Cash sales: sale.total is the exact sales revenue (amount_paid can include change tendered)
        cash_sales_full = sales.filter(payment_method='CASH').aggregate(t=Sum('total'))['t'] or Decimal('0.00')
        # Mixed sales: cash portion is amount_paid
        cash_sales_mixed = sales.filter(payment_method='MIXED').aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
        cash_sales = cash_sales_full + cash_sales_mixed

        # E-Cash & Momo sales
        ecash_sales = sales.filter(payment_method='ECASH').aggregate(t=Sum('total'))['t'] or Decimal('0.00')
        momo_sales = sales.filter(payment_method='MOMO').aggregate(t=Sum('total'))['t'] or Decimal('0.00')

        # Credit Sales (pure credit/invoices + credit portion of mixed sales)
        credit_sales_full = sales.filter(payment_method__in=['CREDIT', 'PENDING_INVOICE']).aggregate(t=Sum('total'))['t'] or Decimal('0.00')
        mixed_sales_qs = sales.filter(payment_method='MIXED')
        mixed_total = mixed_sales_qs.aggregate(t=Sum('total'))['t'] or Decimal('0.00')
        mixed_paid = mixed_sales_qs.aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
        mixed_credit = max(mixed_total - mixed_paid, Decimal('0.00'))
        credit_sales = credit_sales_full + mixed_credit

        # Account for any other unclassified payment methods
        other_sales_qs = sales.exclude(payment_method__in=['CASH', 'ECASH', 'MOMO', 'CREDIT', 'PENDING_INVOICE', 'MIXED'])
        if other_sales_qs.exists():
            other_cash = other_sales_qs.aggregate(t=Sum('amount_paid'))['t'] or Decimal('0.00')
            other_total = other_sales_qs.aggregate(t=Sum('total'))['t'] or Decimal('0.00')
            cash_sales += other_cash
            credit_sales += max(other_total - other_cash, Decimal('0.00'))

        payments_breakdown = {}
        if cash_sales > 0:
            payments_breakdown['Cash'] = cash_sales
        if ecash_sales > 0:
            payments_breakdown['E-Cash'] = ecash_sales
        if momo_sales > 0:
            payments_breakdown['Local Momo'] = momo_sales
        if credit_sales > 0:
            payments_breakdown['Credit (Unpaid)'] = credit_sales

        # 5. Customer Payments
        ct_filter = {
            'tenant': tenant,
            'transaction_type': 'CREDIT',
            'created_at__date__gte': start_date,
            'created_at__date__lte': end_date
        }
        if role_name in ['SHOP_ATTENDANT', 'SHOP_CASHIER']:
            ct_filter['performed_by'] = user
        elif shop_filter_for_inventory:
            ct_filter['customer__shop'] = shop_filter_for_inventory

        customer_payments = CustomerTransaction.objects.filter(**ct_filter).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

        # 6. Funds Transferred to Accountant
        acc_transfer_filter = {
            'tenant': tenant,
            'status': 'CONFIRMED',
            'transfer_type': 'DEPOSIT',
            'confirmed_at__date__gte': start_date,
            'confirmed_at__date__lte': end_date,
        }
        if shop_filter_for_inventory:
            acc_transfer_filter['from_location'] = shop_filter_for_inventory
        elif role_name in ['SHOP_ATTENDANT', 'SHOP_CASHIER']:
            acc_transfer_filter['from_user'] = user

        funds_transferred_to_accountant = CashTransfer.objects.filter(
            **acc_transfer_filter
        ).filter(
            _Q(destination='ACCOUNTANT') | _Q(destination__isnull=True) | ~_Q(destination='BANK')
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR'] and not shop_filter_for_inventory:
            funds_transferred_to_accountant = CashTransfer.objects.filter(
                tenant=tenant,
                status='CONFIRMED',
                transfer_type='DEPOSIT',
                confirmed_at__date__gte=start_date,
                confirmed_at__date__lte=end_date
            ).filter(
                _Q(destination='ACCOUNTANT') | _Q(destination__isnull=True) | ~_Q(destination='BANK')
            ).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

        # 7. Funds Sent to Bank
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
        elif role_name in ['SHOP_ATTENDANT', 'SHOP_CASHIER']:
            bank_ct_filter['from_user'] = user

        cash_to_bank = CashTransfer.objects.filter(**bank_ct_filter).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

        bt_filter = {
            'tenant': tenant,
            'created_at__date__gte': start_date,
            'created_at__date__lte': end_date
        }
        if shop_filter_for_inventory:
            bt_filter['accountant__location'] = shop_filter_for_inventory
        elif role_name in ['SHOP_CASHIER', 'ACCOUNTANT']:
            bt_filter['accountant'] = user

        bank_transfers = BankTransfer.objects.filter(**bt_filter).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')
        funds_sent_to_bank = cash_to_bank + bank_transfers

        # 8. Expenditure
        exp_filter = {
            'request__tenant': tenant,
            'status': 'APPROVED',
            'approved_at__date__gte': start_date,
            'approved_at__date__lte': end_date
        }
        if shop_filter_for_inventory:
            exp_filter['request__location'] = shop_filter_for_inventory

        expenditures = ExpenditureItem.objects.filter(**exp_filter).aggregate(t=Sum('amount'))['t'] or Decimal('0.00')

        summary = {
            'shop_total_sales': shop_total_sales_value,
            'shop_total_sales_value': shop_total_sales_value,
            'total_sales_value': shop_total_sales_value,
            'cash_sales': cash_sales,
            'ecash_sales': ecash_sales,
            'momo_sales': momo_sales,
            'credit_sales': credit_sales,
            'customer_payments': customer_payments,
            'customer_payments_received': customer_payments,
            'shop_customer_payments_received': customer_payments,
            'items_sold_qty': items_sold_qty,
            'shop_items_sold_qty': items_sold_qty,
            'invoice_count': invoice_count,
            'shop_invoice_count': invoice_count,
            'items_dispatched_qty': items_dispatched_qty,
            'shop_items_dispatched_qty': items_dispatched_qty,
            'funds_sent_to_bank': funds_sent_to_bank,
            'shop_bank_transfers_made': funds_sent_to_bank,
            'bank_transfers_made': funds_sent_to_bank,
            'funds_transferred_to_accountant': funds_transferred_to_accountant,
            'transferred_funds_out': funds_transferred_to_accountant,
            'cash_transfers_received': funds_transferred_to_accountant,
            'expenditures': expenditures,
            'shop_expenditures': expenditures,
            'expenditures_disbursed': expenditures,
            'payments_breakdown': payments_breakdown,
        }
        context = {
            'start_date': start_date,
            'end_date': end_date,
            'role_name': role_name,
            'role_display_name': role_name.replace('_', ' ').title() if role_name else 'Unknown',
            'viewing_shop': viewing_shop,
            'stats': sorted_stats,
            'exclude_inactive': exclude_inactive,
            'top_n': top_n_str,
            'shop_id': viewing_shop_id or '',
            **summary,
        }

        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            from apps.core.models import Location
            context['available_shops'] = Location.objects.filter(tenant=tenant, location_type='SHOP', is_active=True)

        if request.GET.get('pdf') == 'true':
            from django.template.loader import get_template
            from django.http import HttpResponse
            from xhtml2pdf import pisa
            from io import BytesIO

            template = get_template('reports/pdf_combined_template.html')
            html = template.render(context)
            result = BytesIO()
            pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
            if not pdf.err:
                response = HttpResponse(result.getvalue(), content_type='application/pdf')
                response['Content-Disposition'] = f'attachment; filename="end_of_day_report_{start_date}.pdf"'
                return response

        if request.GET.get('excel') == 'true':
            import openpyxl
            from django.http import HttpResponse

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "End of Day Report"
            
            ws.append(["End of Day Report"])
            ws.append(["Period:", f"{start_date} to {end_date}"])
            ws.append(["Role:", role_name.replace('_', ' ').title()])
            if viewing_shop:
                ws.append(["Shop:", viewing_shop.name])
            ws.append([])
            
            ws.append(["Financial Summary"])
            for k, v in summary.items():
                if k == 'payments_breakdown':
                    continue
                label = k.replace('_', ' ').title()
                ws.append([label, float(v) if isinstance(v, Decimal) else v])
                
            if 'payments_breakdown' in summary and summary['payments_breakdown']:
                ws.append([])
                ws.append(["Payments Breakdown"])
                for method, amount in summary['payments_breakdown'].items():
                    ws.append([method.replace('_', ' ').title(), float(amount) if isinstance(amount, Decimal) else amount])
            
            ws.append([])
            ws.append(["Detailed Product Breakdown"])
            ws.append(['#', 'Product Name', 'Qty Sold', 'Total Revenue', 'Stock Left'])
            
            for i, item in enumerate(sorted_stats, 1):
                idx = i if item['name'] != 'Others' else '-'
                qty = float(item['qty_sold']) if item['qty_sold'] else 0
                rev = float(item['revenue']) if item['revenue'] else 0
                stock = item['stock_left']
                if isinstance(stock, Decimal):
                    stock = float(stock)
                ws.append([idx, item['name'], qty, rev, stock])
                
            response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = f'attachment; filename="end_of_day_report_{start_date}.xlsx"'
            wb.save(response)
            return response

        return render(request, self.template_name, context)
