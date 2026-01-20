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
        
        # Add notification data
        from apps.notifications.models import Notification
        context['unread_notification_count'] = Notification.get_unread_count(user)
        context['recent_notifications'] = Notification.get_recent_for_user(user, limit=5)
        
        # Calculate cash on hand based on role
        from apps.accounting.models import CashTransfer
        
        if role_name == 'SHOP_ATTENDANT':
            # Cash from current open shift + sales made without a shift
            from apps.sales.models import Shift, Sale
            
            cash_on_hand = Decimal('0')
            
            # 1. Cash from open shift
            open_shift = Shift.objects.filter(
                tenant=tenant,
                attendant=user,
                status='OPEN'
            ).first()
            
            if open_shift:
                shift_cash = Sale.objects.filter(
                    tenant=tenant,
                    shift=open_shift,
                    status='COMPLETED',
                    payment_method='CASH'
                ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                cash_on_hand += open_shift.opening_cash + shift_cash
            
            # 2. Cash from shiftless sales (sales made without opening a shift)
            # These are sales where shift is null and not yet transferred
            shiftless_cash = Sale.objects.filter(
                tenant=tenant,
                attendant=user,
                shift__isnull=True,
                status='COMPLETED',
                payment_method='CASH'
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')
            
            # Add customer cash payments received (even outside shift)
            # Only count explicit (CASH) payments, not (ECASH)
            from apps.customers.models import CustomerTransaction
            customer_payments = CustomerTransaction.objects.filter(
                tenant=tenant,
                performed_by=user,
                transaction_type='CREDIT',
                description__icontains='(CASH)'
            ).exclude(
                description__icontains='ECASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Subtract any pending or confirmed transfers already made
            transferred = CashTransfer.objects.filter(
                tenant=tenant,
                from_user=user,
                status__in=['PENDING', 'CONFIRMED']
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            # Total cash on hand = shift cash + shiftless cash + customer payments - already transferred
            cash_on_hand += shiftless_cash + customer_payments
            cash_on_hand = max(Decimal('0'), cash_on_hand - transferred)
            
            context['cash_on_hand'] = cash_on_hand
        
        elif role_name == 'SHOP_MANAGER':
            # Cash received from attendants (confirmed) minus sent to accountant
            # Plus own sales made directly (without shift)
            # Plus customer payments received in cash
            from apps.sales.models import Sale
            from apps.customers.models import CustomerTransaction
            
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
            
            # Add own shiftless cash sales (made directly by manager)
            own_sales = Sale.objects.filter(
                tenant=tenant,
                attendant=user,
                shift__isnull=True,
                status='COMPLETED',
                payment_method='CASH'
            ).aggregate(total=Sum('total'))['total'] or Decimal('0')
            
            # Add customer cash payments received (payments on account)
            # Only count explicit (CASH) payments, not (ECASH)
            customer_payments = CustomerTransaction.objects.filter(
                tenant=tenant,
                performed_by=user,
                transaction_type='CREDIT',  # CREDIT = payment received
                description__icontains='(CASH)'  # Only cash payments
            ).exclude(
                description__icontains='ECASH'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            context['cash_on_hand'] = received - sent + own_sales + customer_payments
        
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
            # Sum of all positive balances (debt owed to shop)
            total_debt = Customer.objects.filter(
                tenant=tenant,
                is_active=True,
                current_balance__gt=0
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
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'SHOP_MANAGER']:
            try:
                from apps.payments.models import ECashLedger
                
                # Shop managers see their shop's e-cash balance
                if role_name == 'SHOP_MANAGER' and user.location and user.location.location_type == 'SHOP':
                    ecash_balance = ECashLedger.get_shop_balance(tenant, user.location)
                else:
                    # Accountants and auditors see tenant total
                    ecash_balance = ECashLedger.get_current_balance(tenant)
                    
                context['ecash_balance'] = ecash_balance
            except Exception:
                context['ecash_balance'] = Decimal('0')
        
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
