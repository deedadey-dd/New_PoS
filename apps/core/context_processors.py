"""
Context processors for the core app.
"""
from decimal import Decimal
from django.db.models import Sum, Q


def tenant_context(request):
    """
    Add tenant information to template context.
    """
    context = {
        'current_tenant': None,
        'currency_symbol': '$',
        'unread_notification_count': 0,
        'recent_notifications': [],
        'cash_on_hand': None,
        'pending_transfers_count': 0,
    }
    
    if request.user.is_authenticated and hasattr(request.user, 'tenant') and request.user.tenant:
        user = request.user
        tenant = user.tenant
        role_name = user.role.name if user.role else None
        
        context['current_tenant'] = tenant
        context['currency_symbol'] = tenant.currency_symbol
        
        # Add notification data
        from apps.notifications.models import Notification
        context['unread_notification_count'] = Notification.get_unread_count(user)
        context['recent_notifications'] = Notification.get_recent_for_user(user, limit=5)
        
        # Calculate cash on hand based on role
        from apps.accounting.models import CashTransfer
        
        if role_name == 'SHOP_ATTENDANT':
            # Cash from current open shift
            from apps.sales.models import Shift, Sale
            open_shift = Shift.objects.filter(
                tenant=tenant,
                attendant=user,
                status='OPEN'
            ).first()
            
            if open_shift:
                cash_sales = Sale.objects.filter(
                    tenant=tenant,
                    shift=open_shift,
                    status='COMPLETED',
                    payment_method='CASH'
                ).aggregate(total=Sum('total'))['total'] or Decimal('0')
                context['cash_on_hand'] = open_shift.opening_cash + cash_sales
            else:
                context['cash_on_hand'] = Decimal('0')
        
        elif role_name == 'SHOP_MANAGER':
            # Cash received from attendants minus cash sent to accountant
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
        
        elif role_name == 'ACCOUNTANT':
            # All deposits received
            received = CashTransfer.objects.filter(
                tenant=tenant,
                to_user=user,
                transfer_type='DEPOSIT',
                status='CONFIRMED'
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
            
            context['cash_on_hand'] = received
        
        # Pending transfers count (for badge)
        if role_name in ['SHOP_MANAGER', 'ACCOUNTANT', 'ADMIN']:
            context['pending_transfers_count'] = CashTransfer.objects.filter(
                tenant=tenant,
                to_user=user,
                status='PENDING'
            ).count()
    
    return context
