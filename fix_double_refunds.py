import os
import django

# Initialize Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

from apps.accounting.models import CashTransfer, DigitalFundWithdrawal
from django.db import transaction

def run_fix():
    print("Starting double refund deduction fix...")
    
    with transaction.atomic():
        # 1. Find buggy CashTransfers
        # The buggy ones have notes starting with "Refund payout: Sale "
        # New correct ones (cross-payments) have "Refund payout (Cross-payment): Sale "
        buggy_cash_transfers = CashTransfer.objects.filter(
            transfer_type='EXPENDITURE',
            notes__startswith="Refund payout: Sale "
        )
        
        ct_count = buggy_cash_transfers.count()
        print(f"Found {ct_count} buggy CashTransfer (EXPENDITURE) records.")
        
        for ct in buggy_cash_transfers:
            print(f"  - Deleting CashTransfer ID: {ct.id} | Amount: {ct.amount} | Notes: {ct.notes}")
            ct.delete()
            
        # 2. Find buggy DigitalFundWithdrawals
        # The buggy ones have notes starting with "Refund reversal: Sale "
        # New correct ones (cross-payments) have "Refund reversal (Cross-payment): Sale "
        buggy_digital_withdrawals = DigitalFundWithdrawal.objects.filter(
            notes__startswith="Refund reversal: Sale "
        )
        
        dw_count = buggy_digital_withdrawals.count()
        print(f"Found {dw_count} buggy DigitalFundWithdrawal records.")
        
        for dw in buggy_digital_withdrawals:
            print(f"  - Deleting DigitalFundWithdrawal ID: {dw.id} | Amount: {dw.amount} | Notes: {dw.notes}")
            dw.delete()
            
    print("\nFix complete! Duplicate deductions have been successfully reversed.")

if __name__ == '__main__':
    run_fix()
