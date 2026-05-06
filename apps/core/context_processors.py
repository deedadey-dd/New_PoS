"""
Context processors for the core app.
"""
from decimal import Decimal
from django.db.models import Sum, Q


def tenant_context(request):
    """
    Add tenant information to template context.
    """
    from django.conf import settings
    
    context = {
        'current_tenant': None,
        'currency_symbol': '$',
        'unread_notification_count': 0,
        'recent_notifications': [],
        'cash_on_hand': None,
        'pending_transfers_count': 0,
        'role_name': None,
        'whatsapp_number': getattr(settings, 'WHATSAPP_NUMBER', ''),
    }
    
    if request.user.is_authenticated and hasattr(request.user, 'tenant') and request.user.tenant:
        user = request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None
        
        context['current_tenant'] = tenant
        context['currency_symbol'] = tenant.currency_symbol
        context['role_name'] = role_name
        context['shops_can_see_other_stock'] = tenant.shops_can_see_other_stock
        
        # Add notification data
        from apps.notifications.models import Notification
        context['unread_notification_count'] = Notification.get_unread_count(user)
        context['recent_notifications'] = Notification.get_recent_for_user(user, limit=5)
        
        # Calculate cash on hand based on role
        from apps.accounting.models import CashTransfer
        
        if role_name == 'SHOP_ATTENDANT':
            from apps.sales.models import Shift, Sale
            from apps.customers.models import CustomerTransaction
            from apps.accounting.models import ExpenditureItem

            # Find the last cash transfer this attendant made (to know the reset point)
            last_transfer_dt = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status__in=['PENDING', 'CONFIRMED']
            ).order_by('-created_at').values_list('created_at', flat=True).first()

            # Build a filter for shifts that started AFTER the last transfer
            shift_filter = dict(tenant=tenant, attendant=user)
            if last_transfer_dt:
                shift_filter['start_time__gt'] = last_transfer_dt

            # All shifts (open AND closed) since last transfer
            relevant_shifts = Shift.objects.filter(**shift_filter)
            shift_ids = list(relevant_shifts.values_list('pk', flat=True))

            # Cash from all relevant shifts (open + closed)
            shift_cash_sales = Sale.objects.filter(
                tenant=tenant,
                shift__in=shift_ids,
                status='COMPLETED',
                payment_method='CASH'
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')

            shift_mixed_cash = Sale.objects.filter(
                tenant=tenant,
                shift__in=shift_ids,
                status='COMPLETED',
                payment_method='MIXED'
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')

            # Opening cash from all relevant shifts (open + recently closed)
            opening_cash_total = relevant_shifts.aggregate(
                total=Sum('opening_cash')
            )['total'] or Decimal('0')

            # Cash from shiftless sales since last transfer
            shiftless_filter = dict(
                tenant=tenant,
                attendant=user,
                shift__isnull=True,
                status='COMPLETED',
            )
            if last_transfer_dt:
                shiftless_filter['created_at__gt'] = last_transfer_dt

            shiftless_cash_sales = Sale.objects.filter(
                **shiftless_filter, payment_method='CASH'
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')

            shiftless_mixed = Sale.objects.filter(
                **shiftless_filter, payment_method='MIXED'
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')

            # Cash customer payments (account settlements in cash) since last transfer
            cust_filter = dict(
                tenant=tenant,
                performed_by=user,
                transaction_type='CREDIT',
                description__icontains='(CASH)',
            )
            if last_transfer_dt:
                cust_filter['created_at__gt'] = last_transfer_dt

            customer_payments = CustomerTransaction.objects.filter(
                **cust_filter
            ).exclude(description__icontains='ECASH').aggregate(
                total=Sum('amount')
            )['total'] or Decimal('0')

            # Pending/confirmed transfers already sent
            transferred = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status__in=['PENDING', 'CONFIRMED']
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            # Expenses paid from shop cash since last transfer
            exp_filter = dict(
                request__tenant=tenant,
                request__requested_by=user,
                status='APPROVED',
                source_of_funds='SHOP_CASH',
            )
            if last_transfer_dt:
                exp_filter['approved_at__gt'] = last_transfer_dt

            expenses = ExpenditureItem.objects.filter(
                **exp_filter
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            cash_on_hand = (
                opening_cash_total
                + shift_cash_sales
                + shift_mixed_cash
                + shiftless_cash_sales
                + shiftless_mixed
                + customer_payments
                - transferred
                - expenses
            )
            context['cash_on_hand'] = max(Decimal('0'), cash_on_hand)
        
        elif role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            from apps.sales.models import Shift, Sale
            from apps.customers.models import CustomerTransaction

            # 1. All confirmed cash received via CashTransfers from OTHER users (attendant shift closes).
            #    Excludes self-transfers (manager's own old shift closes) since shift sales are
            #    now counted directly via the sale aggregation below.
            received = CashTransfer.objects.filter(
                tenant=tenant,
                to_user=user,
                status='CONFIRMED'
            ).exclude(from_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            # 2. Cash sent upstream to accountant / bank (excludes self-transfers).
            sent = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status__in=['PENDING', 'CONFIRMED']
            ).exclude(to_user=user).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            # 3. Cash from ALL shifts where this user is the attendant (open + closed).
            #    Closed-shift sales are no longer routed through self-transfers, so we
            #    sum them directly here. Open-shift sales are naturally included too.
            all_shifts = Shift.objects.filter(tenant=tenant, attendant=user)
            shift_sale_qs = Sale.objects.filter(
                tenant=tenant,
                shift__in=all_shifts,
                status='COMPLETED',
            )
            all_shift_cash = shift_sale_qs.filter(
                payment_method='CASH'
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')
            all_shift_mixed = shift_sale_qs.filter(
                payment_method='MIXED'
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')

            # Add opening cash from the current OPEN shift only (if included in transfer setting)
            open_shift = Shift.objects.filter(
                tenant=tenant, attendant=user, status='OPEN'
            ).first()
            open_shift_opening_cash = Decimal('0')
            if open_shift and tenant.include_opening_cash_in_transfer:
                open_shift_opening_cash = open_shift.opening_cash

            all_shift_total = all_shift_cash + all_shift_mixed + open_shift_opening_cash

            # 4. Cash from sales THIS USER processed as cashier for OTHER attendants,
            #    since last upstream transfer. Excludes sales where cashier==attendant==user
            #    (direct POS sales) since those are already in shiftless_direct_total /
            #    all_shift_total and would otherwise be double-counted.
            last_transfer_dt = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status__in=['PENDING', 'CONFIRMED']
            ).exclude(to_user=user).order_by('-created_at').values_list(
                'created_at', flat=True
            ).first()

            cashier_sale_filter = dict(
                tenant=tenant, cashier=user, status='COMPLETED'
            )
            if last_transfer_dt:
                cashier_sale_filter['completed_at__gt'] = last_transfer_dt

            cashier_cash = Sale.objects.filter(
                **cashier_sale_filter, payment_method='CASH'
            ).exclude(attendant=user).aggregate(total=Sum('total'))['total'] or Decimal('0')
            cashier_mixed = Sale.objects.filter(
                **cashier_sale_filter, payment_method='MIXED'
            ).exclude(attendant=user).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            cashier_sales_total = cashier_cash + cashier_mixed

            # 5. Shiftless direct sales where this user is the ATTENDANT (no shift opened).
            #    Shift-based sales (open + closed) are captured in all_shift_total.
            #    Cashier-role sales for other attendants are in cashier_sales_total.
            #    Shiftless self-sales are only captured here — count ALL of them because
            #    the manager's upstream transfers (sent) already deduct what was sent out.
            shiftless_cash = Sale.objects.filter(
                tenant=tenant,
                attendant=user,
                shift__isnull=True,
                status='COMPLETED',
                payment_method='CASH',
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')
            shiftless_mixed = Sale.objects.filter(
                tenant=tenant,
                attendant=user,
                shift__isnull=True,
                status='COMPLETED',
                payment_method='MIXED',
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            shiftless_direct_total = shiftless_cash + shiftless_mixed

            # Customer cash payments (payments on account, cash only)
            customer_payments = CustomerTransaction.objects.filter(
                tenant=tenant,
                performed_by=user,
                transaction_type='CREDIT',
                description__icontains='(CASH)'
            ).exclude(description__icontains='ECASH').aggregate(
                total=Sum('amount'))['total'] or Decimal('0')

            # Expenses paid from shop cash
            from apps.accounting.models import ExpenditureItem
            expenses = ExpenditureItem.objects.filter(
                request__tenant=tenant,
                request__requested_by=user,
                status='APPROVED',
                source_of_funds='SHOP_CASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            context['cash_on_hand'] = max(
                Decimal('0'),
                received - sent + all_shift_total + cashier_sales_total
                + shiftless_direct_total + customer_payments - expenses
            )
        
        elif role_name == 'ACCOUNTANT':
            # All deposits received minus any sent out
            received = CashTransfer.objects.filter(
                tenant=tenant,
                to_user=user,
                status='CONFIRMED'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            sent = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status='CONFIRMED'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            context['cash_on_hand'] = received - sent
        
        # Add total credit debt for managers/admin
        if role_name in ['SHOP_MANAGER', 'ADMIN', 'ACCOUNTANT']:
            from apps.customers.models import Customer
            # Base filter: active customers with positive balance (debt owed to shop)
            debt_filter = {
                'tenant': tenant,
                'is_active': True,
                'current_balance__gt': 0,
            }
            # Shop managers only see debt from their own shop's customers
            if role_name == 'SHOP_MANAGER' and user.location and user.location.location_type == 'SHOP':
                debt_filter['shop'] = user.location
            
            total_debt = Customer.objects.filter(
                **debt_filter
            ).aggregate(total=Sum('current_balance'))['total'] or Decimal('0')
            context['total_credit_debt'] = total_debt
        
        # Pending transfers count (for badge)
        if role_name in ['SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN']:
            context['pending_transfers_count'] = CashTransfer.objects.filter(
                tenant=tenant,
                to_user=user,
                status='PENDING'
            ).count()
        
        # E-Cash balance for accountants, managers, and auditors
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'SHOP_MANAGER', 'SHOP_CASHIER']:
            try:
                from apps.payments.models import ECashLedger
                from django.utils import timezone
                
                # Shop managers see their shop's e-cash balance
                if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and user.location and user.location.location_type == 'SHOP':
                    ecash_balance = ECashLedger.get_shop_balance(tenant, user.location, 'ECASH')
                    momo_balance = ECashLedger.get_shop_balance(tenant, user.location, 'MOMO')
                else:
                    # Accountants and auditors see tenant total
                    ecash_balance = ECashLedger.get_current_balance(tenant, 'ECASH')
                    momo_balance = ECashLedger.get_current_balance(tenant, 'MOMO')
                    
                context['ecash_balance'] = ecash_balance
                context['momo_balance'] = momo_balance
                
                # Calculate monthly totals
                now = timezone.now()
                monthly_txs = ECashLedger.objects.filter(
                    tenant=tenant,
                    transaction_type='PAYMENT',
                    created_at__year=now.year,
                    created_at__month=now.month
                )
                
                if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and user.location and user.location.location_type == 'SHOP':
                    monthly_txs = monthly_txs.filter(shop=user.location)
                    
                monthly_ecash = monthly_txs.filter(wallet_type='ECASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                monthly_momo = monthly_txs.filter(wallet_type='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                context['monthly_ecash'] = monthly_ecash
                context['monthly_momo'] = monthly_momo
            except Exception:
                context['ecash_balance'] = Decimal('0')
                context['momo_balance'] = Decimal('0')
                context['monthly_ecash'] = Decimal('0')
                context['monthly_momo'] = Decimal('0')
        
        # Low stock products for the user's location (Stock Alerts)
        if user.location and role_name in ['SHOP_MANAGER', 'SHOP_ATTENDANT', 'STORES_MANAGER', 'PRODUCTION_MANAGER', 'ADMIN']:
            from apps.inventory.models import Product, InventoryLedger
            from django.db.models import Value, Case, When, CharField
            
            user_location = user.location
            
            # Get products with their stock at user's location
            products = Product.objects.filter(
                tenant=tenant,
                is_active=True,
                reorder_level__gt=0  # Only products with a reorder level set
            )
            
            low_stock_list = []
            for product in products:
                stock_qty = product.get_stock_at_location(user_location)
                
                if stock_qty <= product.reorder_level:
                    # Determine severity
                    if stock_qty <= 0:
                        severity = 'critical'  # Out of stock - red
                    else:
                        severity = 'warning'  # Low stock - yellow
                    
                    low_stock_list.append({
                        'id': product.pk,
                        'name': product.name,
                        'quantity': stock_qty,
                        'reorder_level': product.reorder_level,
                        'severity': severity,
                    })
            
            # Sort by severity (critical first) then by quantity
            low_stock_list.sort(key=lambda x: (0 if x['severity'] == 'critical' else 1, x['quantity']))
            
            context['low_stock_products'] = low_stock_list[:10]  # Limit to top 10
            context['low_stock_count'] = len(low_stock_list)
    
    return context
