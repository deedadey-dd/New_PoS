"""
Role-specific navigation resolver for Notifications and Bulletin Posts.
Ensures that notifications in /notifications/ and bulletins in /notifications/bulletin/
lead to corresponding, actionable pages specific to the user's role.
"""
import logging
from django.urls import reverse

logger = logging.getLogger(__name__)


def resolve_target_url(item, user):
    """
    Resolve the role-specific destination URL for a Notification or BulletinPost.

    Args:
        item: Notification or BulletinPost instance (or duck-typed object).
        user: The User instance requesting the URL.

    Returns:
        str: Absolute path / URL to navigate to, or None if user not authenticated.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return None

    role = getattr(user, 'role', None)
    role_name = role.name if role else ''
    is_superadmin = getattr(user, 'is_superuser', False) or role_name in ['SUPER_ADMIN', 'TENANT_MANAGER']

    ref_type = (getattr(item, 'reference_type', '') or '').strip()
    ref_id = getattr(item, 'reference_id', None)
    notif_type = getattr(item, 'notification_type', '') or ''
    post_type = getattr(item, 'post_type', '') or ''
    title = (getattr(item, 'title', '') or '').strip()
    title_lower = title.lower()

    # ---------------------------------------------------------
    # 0. SUPER ADMIN / TENANT MANAGER RESOLUTION
    # ---------------------------------------------------------
    if is_superadmin:
        if ref_type == 'Tenant' and ref_id:
            try:
                return reverse('superadmin:tenant_detail', kwargs={'pk': ref_id})
            except Exception:
                pass
        if role_name == 'TENANT_MANAGER':
            try:
                return reverse('subscriptions:tm_dashboard')
            except Exception:
                pass
        if post_type == 'SYSTEM_UPDATE' or 'system update' in title_lower or 'feature' in title_lower:
            try:
                return reverse('core:documentation')
            except Exception:
                pass
        try:
            return reverse('superadmin:dashboard')
        except Exception:
            return reverse('core:dashboard')

    # ---------------------------------------------------------
    # 1. REFUND REQUESTS & RETURNS
    # ---------------------------------------------------------
    if (ref_type in ['RefundRequest', 'Refund', 'refund'] or 
        notif_type == 'REFUND_REQUEST' or 
        'refund' in title_lower or 
        'partial return' in title_lower or 
        'partial refund' in title_lower):
        
        # Cashier: Needs to pay out approved refunds
        if role_name == 'SHOP_CASHIER':
            return reverse('sales:refund_request_list') + '?status=PENDING_PAYOUT'
        # Accountants, Admins, Auditors: Approve or audit refund requests
        elif role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            return reverse('sales:refund_request_list')
        # Shop Managers: Track and initiate store refund requests
        elif role_name == 'SHOP_MANAGER':
            return reverse('sales:refund_request_list')
        # Shop Attendant: POS or sales
        elif role_name == 'SHOP_ATTENDANT':
            return reverse('sales:pos')
        else:
            return reverse('sales:refund_request_list')

    # ---------------------------------------------------------
    # 2. TRANSFERS (Location-to-location inventory transfers)
    # ---------------------------------------------------------
    if (ref_type in ['Transfer', 'transfer'] or 
        notif_type in ['TRANSFER_SENT', 'TRANSFER_RECEIVED', 'TRANSFER_DISPUTED'] or 
        ('transfer' in title_lower and 'cash' not in title_lower and 'bank' not in title_lower)):
        
        if ref_id:
            try:
                return reverse('transfers:transfer_detail', kwargs={'pk': ref_id})
            except Exception:
                pass
        return reverse('transfers:transfer_list')

    # ---------------------------------------------------------
    # 3. STOCK REQUESTS
    # ---------------------------------------------------------
    if (ref_type in ['StockRequest', 'Stock Request', 'stock_request'] or 
        notif_type == 'STOCK_REQUEST' or 
        'stock request' in title_lower):
        
        if ref_id:
            try:
                return reverse('transfers:stock_request_detail', kwargs={'pk': ref_id})
            except Exception:
                pass
        return reverse('transfers:stock_request_list')

    # ---------------------------------------------------------
    # 4. EXPENDITURE REQUESTS & VOUCHERS
    # ---------------------------------------------------------
    if (ref_type in ['ExpenditureRequest', 'Expenditure', 'expenditure'] or 
        notif_type == 'EXPENDITURE_REQUEST' or 
        'expenditure' in title_lower or 
        'voucher' in title_lower):
        
        if ref_id and role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR', 'SHOP_MANAGER']:
            try:
                return reverse('accounting:expenditure_detail', kwargs={'pk': ref_id})
            except Exception:
                pass
        return reverse('accounting:expenditure_list')

    # ---------------------------------------------------------
    # 5. CASH TRANSFERS & DEPOSITS
    # ---------------------------------------------------------
    if (ref_type in ['CashTransfer', 'cash_transfer'] or 
        notif_type == 'CASH_TRANSFER' or 
        'cash transfer' in title_lower or 
        'cash deposit' in title_lower):
        
        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR', 'SHOP_MANAGER', 'SHOP_CASHIER', 'SHOP_ATTENDANT']:
            return reverse('accounting:cash_transfer_list')
        return reverse('accounting:accountant_dashboard')

    # ---------------------------------------------------------
    # 6. STOCK ADJUSTMENTS
    # ---------------------------------------------------------
    if (ref_type in ['StockAdjustment', 'stock_adjustment'] or 
        notif_type == 'STOCK_ADJUSTMENT' or 
        'stock adjustment' in title_lower or 
        ('adjustment' in title_lower and 'price' not in title_lower)):
        
        return reverse('inventory:adjustment_history')

    # ---------------------------------------------------------
    # 7. PRICE CHANGES & PRICE ALERTS
    # ---------------------------------------------------------
    if (ref_type in ['Product', 'product'] or 
        notif_type == 'PRICE_CHANGE' or 
        post_type == 'PRICE_ALERT' or 
        'price change' in title_lower or 
        'price alert' in title_lower or 
        'pricing' in title_lower):
        
        if role_name in ['ACCOUNTANT', 'ADMIN']:
            return reverse('inventory:shop_price_list')
        elif role_name == 'SHOP_MANAGER':
            return reverse('inventory:price_change_center')
        elif role_name in ['SHOP_CASHIER', 'SHOP_ATTENDANT']:
            return reverse('sales:pos')
        elif role_name == 'AUDITOR':
            return reverse('accounting:price_history')
        else:
            return reverse('inventory:price_change_center')

    # ---------------------------------------------------------
    # 8. LOW STOCK ALERTS
    # ---------------------------------------------------------
    if notif_type == 'LOW_STOCK' or 'low stock' in title_lower:
        if role_name == 'STORES_MANAGER':
            return reverse('transfers:stock_request_list')
        elif role_name == 'SHOP_MANAGER':
            return reverse('inventory:product_list') + '?low_stock=1'
        else:
            return reverse('inventory:product_list') + '?low_stock=1'

    # ---------------------------------------------------------
    # 9. EXPIRY WARNINGS & BATCHES
    # ---------------------------------------------------------
    if (ref_type in ['Batch', 'batch'] or 
        notif_type == 'EXPIRY_WARNING' or 
        'expiry' in title_lower or 
        'batch edit' in title_lower):
        
        if ref_id and ref_type in ['Batch', 'batch']:
            try:
                return reverse('inventory:batch_detail', kwargs={'pk': ref_id})
            except Exception:
                pass
        return reverse('inventory:batch_list')

    # ---------------------------------------------------------
    # 10. SALES, DISPATCHES, AND INVOICES
    # ---------------------------------------------------------
    if (ref_type in ['Sale', 'sale'] or 
        notif_type == 'INVOICE_PAID' or 
        'invoice paid' in title_lower or 
        'dispatch' in title_lower):
        
        if role_name in ['STORES_MANAGER']:
            return reverse('sales:dispatch_list')
        elif role_name == 'SHOP_MANAGER':
            if 'dispatch' in title_lower or 'invoice' in title_lower:
                return reverse('sales:dispatch_list')
            return reverse('sales:sale_list')
        elif role_name in ['SHOP_CASHIER', 'SHOP_ATTENDANT']:
            return reverse('sales:sale_list')
        elif role_name in ['ACCOUNTANT', 'ADMIN']:
            return reverse('sales:sale_list')
        else:
            return reverse('sales:sale_list')

    # ---------------------------------------------------------
    # 11. E-CASH WITHDRAWALS
    # ---------------------------------------------------------
    if ref_type in ['ECashWithdrawal', 'ecash_withdrawal'] or 'e-cash withdrawal' in title_lower:
        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            return reverse('accounting:digital_confirmations')
        elif role_name in ['SHOP_MANAGER', 'SHOP_CASHIER']:
            return reverse('accounting:cash_transfer_list')
        return reverse('accounting:accountant_dashboard')

    # ---------------------------------------------------------
    # 12. SUBSCRIPTIONS & ACCOUNT STATUS
    # ---------------------------------------------------------
    if (notif_type in ['SUBSCRIPTION_EXPIRY', 'SUBSCRIPTION_DEACTIVATED', 'ACCOUNT_LOCKED'] or 
        'subscription' in title_lower or 
        'account locked' in title_lower):
        
        if role_name == 'TENANT_MANAGER':
            return reverse('subscriptions:tm_dashboard')
        elif role_name in ['ADMIN', 'SUPER_ADMIN']:
            return reverse('subscriptions:status')
        return reverse('subscriptions:status')

    # ---------------------------------------------------------
    # 13. SYSTEM UPDATES & DOCUMENTATION
    # ---------------------------------------------------------
    if post_type == 'SYSTEM_UPDATE' or 'system update' in title_lower or 'feature update' in title_lower:
        return reverse('core:documentation')

    # ---------------------------------------------------------
    # 14. DEFAULT ROLE FALLBACKS
    # ---------------------------------------------------------
    if role_name in ['ACCOUNTANT', 'SHOP_CASHIER']:
        return reverse('accounting:accountant_dashboard')
    elif role_name == 'AUDITOR':
        return reverse('core:auditor_dashboard')
    elif role_name in ['SHOP_ATTENDANT']:
        return reverse('sales:pos')
    elif role_name in ['STORES_MANAGER']:
        return reverse('transfers:transfer_list')
    else:
        return reverse('core:dashboard')


def resolve_action_label(item, user):
    """
    Return a concise, context-rich label for the action button/link based on user role.

    Args:
        item: Notification or BulletinPost instance (or duck-typed object).
        user: The User instance viewing the item.

    Returns:
        str: User-friendly action button text (e.g., 'Review Refund Request').
    """
    if not user:
        return "View Details"

    role = getattr(user, 'role', None)
    role_name = role.name if role else ''
    is_superadmin = getattr(user, 'is_superuser', False) or role_name in ['SUPER_ADMIN', 'TENANT_MANAGER']

    if is_superadmin:
        if role_name == 'TENANT_MANAGER':
            return "Tenant Dashboard"
        return "View Platform Details"

    ref_type = (getattr(item, 'reference_type', '') or '').strip()
    notif_type = getattr(item, 'notification_type', '') or ''
    post_type = getattr(item, 'post_type', '') or ''
    title = (getattr(item, 'title', '') or '').strip()
    title_lower = title.lower()

    if (ref_type in ['RefundRequest', 'Refund', 'refund'] or 
        notif_type == 'REFUND_REQUEST' or 
        'refund' in title_lower or 
        'partial return' in title_lower or 
        'partial refund' in title_lower):
        if role_name == 'SHOP_CASHIER':
            return "Disburse Refund Payout"
        elif role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            return "Review Refund Request"
        elif role_name == 'SHOP_MANAGER':
            return "View Refund Status"
        return "View Refund Request"

    if (ref_type in ['Transfer', 'transfer'] or 
        notif_type in ['TRANSFER_SENT', 'TRANSFER_RECEIVED', 'TRANSFER_DISPUTED'] or 
        ('transfer' in title_lower and 'cash' not in title_lower and 'bank' not in title_lower)):
        return "View Transfer Details"

    if (ref_type in ['StockRequest', 'Stock Request', 'stock_request'] or 
        notif_type == 'STOCK_REQUEST' or 
        'stock request' in title_lower):
        if role_name in ['STORES_MANAGER', 'ADMIN']:
            return "Review Stock Request"
        elif role_name == 'SHOP_MANAGER':
            return "Track Stock Request"
        return "View Stock Requests"

    if (ref_type in ['ExpenditureRequest', 'Expenditure', 'expenditure'] or 
        notif_type == 'EXPENDITURE_REQUEST' or 
        'expenditure' in title_lower or 
        'voucher' in title_lower):
        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            return "Review Expenditure Voucher"
        elif role_name == 'SHOP_MANAGER':
            return "View Expenditure Voucher"
        return "View Expenditures"

    if (ref_type in ['CashTransfer', 'cash_transfer'] or 
        notif_type == 'CASH_TRANSFER' or 
        'cash transfer' in title_lower or 
        'cash deposit' in title_lower):
        return "Confirm / View Transfer"

    if (ref_type in ['StockAdjustment', 'stock_adjustment'] or 
        notif_type == 'STOCK_ADJUSTMENT' or 
        'stock adjustment' in title_lower or 
        ('adjustment' in title_lower and 'price' not in title_lower)):
        if role_name in ['ACCOUNTANT', 'AUDITOR', 'ADMIN']:
            return "Review Stock Adjustments"
        return "View Stock Adjustments"

    if (ref_type in ['Product', 'product'] or 
        notif_type == 'PRICE_CHANGE' or 
        post_type == 'PRICE_ALERT' or 
        'price change' in title_lower or 
        'price alert' in title_lower or 
        'pricing' in title_lower):
        if role_name in ['ACCOUNTANT', 'ADMIN']:
            return "Manage Pricing"
        elif role_name == 'SHOP_MANAGER':
            return "Review Price Changes"
        elif role_name in ['SHOP_CASHIER', 'SHOP_ATTENDANT']:
            return "Go to POS"
        elif role_name == 'AUDITOR':
            return "View Price History"
        return "View Price Changes"

    if notif_type == 'LOW_STOCK' or 'low stock' in title_lower:
        if role_name == 'STORES_MANAGER':
            return "Review Low Stock"
        return "View Low Stock Products"

    if (ref_type in ['Batch', 'batch'] or 
        notif_type == 'EXPIRY_WARNING' or 
        'expiry' in title_lower or 
        'batch edit' in title_lower):
        return "View Batch Details"

    if (ref_type in ['Sale', 'sale'] or 
        notif_type == 'INVOICE_PAID' or 
        'invoice paid' in title_lower or 
        'dispatch' in title_lower):
        if role_name in ['STORES_MANAGER', 'SHOP_MANAGER']:
            return "Go to Dispatches"
        return "View Sales Details"

    if ref_type in ['ECashWithdrawal', 'ecash_withdrawal'] or 'e-cash withdrawal' in title_lower:
        if role_name in ['ACCOUNTANT', 'ADMIN', 'AUDITOR']:
            return "Review Digital Confirmation"
        return "View Cash Transfers"

    if (notif_type in ['SUBSCRIPTION_EXPIRY', 'SUBSCRIPTION_DEACTIVATED', 'ACCOUNT_LOCKED'] or 
        'subscription' in title_lower or 
        'account locked' in title_lower):
        return "Manage Subscription"

    if post_type == 'SYSTEM_UPDATE' or 'system update' in title_lower or 'feature update' in title_lower:
        return "View Documentation"

    return "View Details"
