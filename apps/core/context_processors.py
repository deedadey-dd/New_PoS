"""
Context processors for the core app.
"""
from decimal import Decimal
from django.db.models import Sum, Q


def platform_context(request):
    """
    Add platform branding details to template context.
    """
    from django.conf import settings
    return {
        'PLATFORM_COMPANY_NAME': getattr(settings, 'PLATFORM_COMPANY_NAME', 'HendAxis PoS'),
        'PLATFORM_EMAIL': getattr(settings, 'PLATFORM_EMAIL', 'sales@hendaxis.com'),
        'PLATFORM_PHONE': getattr(settings, 'PLATFORM_PHONE', '+233538127939'),
        'FRONTEND_URL': getattr(settings, 'FRONTEND_URL', 'https://pos.hendaxis.com'),
        'TINYMCE_API_KEY': getattr(settings, 'TINYMCE_API_KEY', 'no-api-key'),
        'GOOGLE_SITE_VERIFICATION': getattr(settings, 'GOOGLE_SITE_VERIFICATION', ''),
    }


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
        from apps.notifications.models import Notification, BulletinPost, BulletinRead
        context['unread_notification_count'] = Notification.get_unread_count(user)
        context['recent_notifications'] = Notification.get_recent_for_user(user, limit=5)
        
        # Calculate bulletin unread count
        # This is a slightly simplified count for performance (we can just call the view's query)
        from django.db.models import Q
        qs = BulletinPost.objects.filter(tenant=tenant, is_active=True)
        role_filter = Q(target_roles__isnull=True)
        if user.role:
            role_filter |= Q(target_roles=user.role)
            
        loc_filter = Q(target_locations__isnull=True)
        if user.location:
            loc_filter |= Q(target_locations=user.location)
            
        if role_name != 'ADMIN':
            qs = qs.filter(role_filter, loc_filter).distinct()
            
        read_post_ids = BulletinRead.objects.filter(user=user).values_list('bulletin_post_id', flat=True)
        unread_qs = qs.exclude(id__in=read_post_ids).order_by('-created_at')
        context['bulletin_unread_count'] = unread_qs.count()
        context['latest_unread_bulletin'] = unread_qs.first()

        
        # Calculate cash on hand based on role
        from apps.accounting.models import CashTransfer
        
        if role_name == 'SHOP_ATTENDANT':
            # Cash currently held by this attendant:
            #   (a) Cash from their currently OPEN shift (opening_cash + cash sales so far)
            #   (b) Cash from shiftless sales (no shift opened) — stays until manually transferred
            #   (c) Customer cash payments received
            #   minus any MANUAL transfers out (not shift-close transfers, which are
            #        implicitly removed from cash_on_hand when the shift status → CLOSED)
            from apps.sales.models import Shift, Sale

            cash_on_hand = Decimal('0')

            # 1. Cash from the current OPEN shift
            open_shift = Shift.objects.filter(
                tenant=tenant,
                attendant=user,
                status='OPEN'
            ).first()

            if open_shift:
                cash_sales = Sale.objects.filter(
                    tenant=tenant,
                    shift=open_shift,
                    status__in=['COMPLETED', 'PENDING_DISPATCH'],
                    payment_method='CASH'
                ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                mixed_cash = Sale.objects.filter(
                    tenant=tenant,
                    shift=open_shift,
                    status__in=['COMPLETED', 'PENDING_DISPATCH'],
                    payment_method='MIXED'
                ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                cash_on_hand += open_shift.opening_cash + cash_sales + mixed_cash

            # 2. Cash from shiftless sales (shift=None) — remains until attendant
            #    manually transfers it to the manager
            shiftless_cash_sales = Sale.objects.filter(
                tenant=tenant,
                attendant=user,
                shift__isnull=True,
                status__in=['COMPLETED', 'PENDING_DISPATCH'],
                payment_method='CASH'
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')
            shiftless_mixed = Sale.objects.filter(
                tenant=tenant,
                attendant=user,
                shift__isnull=True,
                status__in=['COMPLETED', 'PENDING_DISPATCH'],
                payment_method='MIXED'
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            cash_on_hand += shiftless_cash_sales + shiftless_mixed

            # 3. Customer cash payments received (on-account payments in CASH)
            from apps.customers.models import CustomerTransaction
            customer_payments = CustomerTransaction.objects.filter(
                tenant=tenant,
                performed_by=user,
                transaction_type='CREDIT',
                description__icontains='(CASH)'
            ).exclude(
                description__icontains='ECASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            cash_on_hand += customer_payments

            # 4. Deduct ONLY manual (non-shift-close) transfers.
            #    Shift-close transfers are auto-tagged with "Shift closing deposit - Shift #".
            #    When a shift closes, the shift's cash is already removed from cash_on_hand
            #    (because we only count OPEN shifts above), so deducting the shift-close
            #    transfer again would cause a false double-subtraction.
            manual_transferred = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status__in=['PENDING', 'CONFIRMED']
            ).exclude(
                notes__startswith='Shift closing deposit'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

            cash_on_hand = max(Decimal('0'), cash_on_hand - manual_transferred)

            context['cash_on_hand'] = cash_on_hand

        
        elif role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            # Cash received from attendants (confirmed transfers TO this manager)
            # Plus own cash sales (with or without shift)
            # Plus customer cash payments received
            # Minus cash sent out (to accountant, expenditures)
            from apps.sales.models import Shift, Sale
            from apps.customers.models import CustomerTransaction
            
            # Transfers IN from other users (attendants depositing to this manager)
            # Exclude self-transfers (from_user == to_user) which cancel out
            received = CashTransfer.objects.filter(
                tenant=tenant,
                to_user=user,
                status='CONFIRMED'
            ).exclude(
                from_user=user  # exclude any legacy self-transfers
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Transfers OUT from this manager (to accountant, expenditures)
            # Exclude self-transfers
            sent = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status='CONFIRMED',
                transfer_type__in=['DEPOSIT', 'EXPENDITURE'],
            ).exclude(
                to_user=user  # exclude any legacy self-transfers
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Own cash collected from sales (either as cashier or attendant)
            cash_sales = Sale.objects.filter(
                tenant=tenant,
                status__in=['COMPLETED', 'PENDING_DISPATCH'],
                payment_method='CASH'
            ).filter(
                Q(cashier=user) | Q(cashier__isnull=True, attendant=user)
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')
            
            mixed_sales = Sale.objects.filter(
                tenant=tenant,
                status__in=['COMPLETED', 'PENDING_DISPATCH'],
                payment_method='MIXED'
            ).filter(
                Q(cashier=user) | Q(cashier__isnull=True, attendant=user)
            ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
            
            own_sales = cash_sales + mixed_sales
            
            # Opening cash from the currently OPEN shift run by this user
            open_shift_opening_cash = Shift.objects.filter(
                tenant=tenant,
                attendant=user,
                status='OPEN'
            ).aggregate(total=Sum('opening_cash'))['total'] or Decimal('0')
            
            # Customer cash payments received (payments on account in cash)
            customer_payments = CustomerTransaction.objects.filter(
                tenant=tenant,
                performed_by=user,
                transaction_type='CREDIT',
                description__icontains='(CASH)'
            ).exclude(
                description__icontains='ECASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Deduct cash transferred to the bank
            from apps.accounting.models import BankTransfer
            banked_cash = BankTransfer.objects.filter(
                tenant=tenant,
                accountant=user,
                fund_source='CASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Check if using strict sales workflow
            if not tenant.use_strict_sales_workflow and user.location:
                # In standard workflow, the shop manager holds all of the shop's cash
                location = user.location
                
                # All time cash sales at this location
                all_sales_agg = Sale.objects.filter(tenant=tenant, shop=location, status__in=['COMPLETED', 'PENDING_DISPATCH']).aggregate(
                    cash_sales=Sum('total', filter=Q(payment_method='CASH')),
                    mixed_paid=Sum('amount_paid', filter=Q(payment_method='MIXED')),
                )
                cash_sales_val = (all_sales_agg['cash_sales'] or Decimal('0')) + (all_sales_agg['mixed_paid'] or Decimal('0'))
                
                # Customer Cash Payments
                customer_payments = CustomerTransaction.objects.filter(
                    tenant=tenant,
                    performed_by__location=location,
                    transaction_type='CREDIT',
                    description__icontains='(CASH)'
                ).exclude(description__icontains='ECASH').exclude(description__icontains='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')

                # Cash transferred OUT of the location (deposits to accountant, expenditures)
                deposits = CashTransfer.objects.filter(
                    tenant=tenant, from_location=location, transfer_type='DEPOSIT', status='CONFIRMED'
                ).exclude(
                    to_user__role__name__in=['SHOP_MANAGER', 'SHOP_ATTENDANT', 'SHOP_CASHIER']
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

                expenditures = CashTransfer.objects.filter(
                    tenant=tenant, from_location=location, transfer_type='EXPENDITURE', status='CONFIRMED'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                # Floats received at the location
                floats_received = CashTransfer.objects.filter(
                    tenant=tenant, to_location=location, transfer_type='FLOAT', status='CONFIRMED'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                # Bank transfers out
                from apps.accounting.models import BankTransfer
                banked_cash = BankTransfer.objects.filter(
                    tenant=tenant,
                    accountant=user,
                    fund_source='CASH'
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                
                context['cash_on_hand'] = max(Decimal('0'), cash_sales_val + customer_payments + floats_received - deposits - expenditures - banked_cash)
            else:
                # Strict workflow: Cash is tracked individually per user (attendants transfer to cashiers/managers)
                context['cash_on_hand'] = max(Decimal('0'), (
                    received
                    - sent
                    + own_sales
                    + customer_payments
                    + open_shift_opening_cash
                    - banked_cash
                ))
        
        elif role_name == 'ACCOUNTANT':
            # All deposits received minus any sent out
            received = CashTransfer.objects.filter(
                tenant=tenant,
                to_user=user,
                status='CONFIRMED'
            ).exclude(
                transfer_type='EXPENDITURE'  # Exclude shop-cash expenditures
            ).exclude(
                destination='BANK'  # Exclude cash deposited directly to bank
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            sent = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status='CONFIRMED'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Subtract Bank Transfers
            from apps.accounting.models import BankTransfer
            banked_cash = BankTransfer.objects.filter(tenant=tenant, fund_source='CASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Add direct customer cash payments received by accountant
            from apps.customers.models import CustomerTransaction
            customer_cash_payments = CustomerTransaction.objects.filter(
                tenant=tenant,
                performed_by=user,
                transaction_type='CREDIT',
                description__icontains='(CASH)'
            ).exclude(
                description__icontains='ECASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            context['cash_on_hand'] = max(Decimal('0'), received - sent - banked_cash + customer_cash_payments)
        
        # Add total credit debt for managers/admin
        if role_name in ['SHOP_MANAGER', 'ADMIN', 'ACCOUNTANT', 'SHOP_CASHIER']:
            from apps.customers.models import Customer
            # Base filter: active customers with positive balance (debt owed to shop)
            debt_filter = {
                'tenant': tenant,
                'is_active': True,
                'current_balance__gt': 0,
            }
            # Shop managers only see debt from their own shop's customers
            if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and user.location and user.location.location_type == 'SHOP':
                debt_filter['shop'] = user.location
            
            total_debt = Customer.objects.filter(
                **debt_filter
            ).aggregate(total=Sum('current_balance'))['total'] or Decimal('0')
            context['total_credit_debt'] = total_debt
        
        # Pending transfers count (for badge)
        if role_name in ['SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN', 'SHOP_CASHIER']:
            context['pending_transfers_count'] = CashTransfer.objects.filter(
                tenant=tenant,
                to_user=user,
                status='PENDING'
            ).count()
        
        # Digital Balances (E-Cash and Momo)
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'SHOP_MANAGER', 'SHOP_CASHIER']:
            from apps.sales.models import Sale
            from apps.customers.models import CustomerTransaction
            
            try:
                # E-CASH BALANCE
                ecash_sales_q = Q(tenant=tenant, status__in=['COMPLETED', 'PENDING_DISPATCH'], payment_method='ECASH')
                ecash_ct_q = Q(tenant=tenant, transaction_type='CREDIT', description__icontains='ECASH')
                
                if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and user.location and user.location.location_type == 'SHOP':
                    # Shop Manager sees current e-cash balance of their shop based on the Ledger
                    from apps.payments.models import ECashLedger
                    context['ecash_balance'] = ECashLedger.get_shop_balance(tenant, user.location)
                else:
                    # Accountant sees Total Withdrawn ECash minus BANK TRANSFERS
                    from apps.payments.models import ECashWithdrawal
                    total_withdrawn = ECashWithdrawal.objects.filter(tenant=tenant, status='COMPLETED').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    # Add legacy DigitalFundWithdrawals just in case
                    from apps.accounting.models import DigitalFundWithdrawal
                    legacy_withdrawn = DigitalFundWithdrawal.objects.filter(tenant=tenant, fund_source='ECASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    # Add direct customer E-Cash payments received by accountant
                    customer_ecash_payments = CustomerTransaction.objects.filter(
                        tenant=tenant,
                        performed_by=user,
                        transaction_type='CREDIT',
                        description__icontains='ECASH'
                    ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    # Subtract Bank Transfers
                    from apps.accounting.models import BankTransfer
                    banked_ecash = BankTransfer.objects.filter(tenant=tenant, fund_source='ECASH').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                    
                    context['ecash_balance'] = (total_withdrawn + legacy_withdrawn + customer_ecash_payments) - banked_ecash
            except Exception as e:
                context['ecash_balance'] = Decimal('0')
                
            try:
                # MOMO BALANCE
                if tenant.allow_momo_payments:
                    momo_sales_q = Q(tenant=tenant, status__in=['COMPLETED', 'PENDING_DISPATCH'], payment_method='MOMO')
                    momo_ct_q = Q(tenant=tenant, transaction_type='CREDIT', description__icontains='MOMO')
                    
                    if role_name in ['SHOP_MANAGER', 'SHOP_CASHIER'] and user.location and user.location.location_type == 'SHOP':
                        # Shop Manager sees ALL momo MINUS DigitalFundWithdrawal
                        shop_sales = Sale.objects.filter(momo_sales_q, shop=user.location).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')
                        shop_ct = CustomerTransaction.objects.filter(momo_ct_q, performed_by__location=user.location).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                        from apps.accounting.models import DigitalFundWithdrawal
                        withdrawn = DigitalFundWithdrawal.objects.filter(tenant=tenant, shop=user.location, fund_source='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                        context['momo_balance'] = shop_sales + shop_ct - withdrawn
                    else:
                        # Accountant sees Total Withdrawn Momo minus BANK TRANSFERS
                        from apps.accounting.models import DigitalFundWithdrawal
                        total_withdrawn = DigitalFundWithdrawal.objects.filter(tenant=tenant, fund_source='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                        
                        # Add direct customer Momo payments received by accountant
                        customer_momo_payments = CustomerTransaction.objects.filter(
                            tenant=tenant,
                            performed_by=user,
                            transaction_type='CREDIT',
                            description__icontains='MOMO'
                        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
                        
                        # Subtract Bank Transfers
                        from apps.accounting.models import BankTransfer
                        banked_momo = BankTransfer.objects.filter(tenant=tenant, fund_source='MOMO').aggregate(total=Sum('amount'))['total'] or Decimal('0')
                        
                        context['momo_balance'] = (total_withdrawn + customer_momo_payments) - banked_momo
            except Exception:
                context['momo_balance'] = Decimal('0')
        
        # Low stock products for the user's location (Stock Alerts)
        if user.location and role_name in ['SHOP_MANAGER', 'SHOP_ATTENDANT', 'STORES_MANAGER', 'PRODUCTION_MANAGER', 'ADMIN', 'SHOP_CASHIER']:
            from apps.inventory.models import Product, InventoryLedger
            
            user_location = user.location
            
            # Efficiently get all stock at this location
            stock_data = InventoryLedger.objects.filter(
                tenant=tenant,
                location=user_location
            ).values('product_id').annotate(
                total_qty=Sum('quantity')
            )
            stock_map = {item['product_id']: item['total_qty'] or Decimal('0') for item in stock_data}
            
            # Get products with their stock at user's location
            products = Product.objects.filter(
                tenant=tenant,
                is_active=True,
                reorder_level__gt=0  # Only products with a reorder level set
            ).only('id', 'name', 'reorder_level')
            
            low_stock_list = []
            for product in products:
                stock_qty = stock_map.get(product.id, Decimal('0'))
                
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
            
        # Price Change Alerts for Shop Manager
        if role_name == 'SHOP_MANAGER':
            from datetime import timedelta
            from django.utils import timezone
            fourteen_days_ago = timezone.now() - timedelta(days=14)
            price_notifications = Notification.objects.filter(
                user=user, 
                notification_type='PRICE_CHANGE', 
                is_read=False,
                created_at__gte=fourteen_days_ago
            ).order_by('-created_at')
            context['price_change_notifications'] = price_notifications[:10]
            context['price_change_count'] = price_notifications.count()
    
    return context
