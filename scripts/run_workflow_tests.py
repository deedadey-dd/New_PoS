import os
import sys
import django
from django.db import transaction

# Add project root to python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

# Initialize Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

import unittest
from django.test.utils import setup_test_environment, teardown_test_environment
setup_test_environment()

from apps.sales.test_workflows import WorkflowIntegrationTests
from apps.inventory.tests.test_pricing_modes import PricingControlModeTests
from apps.core.tests.test_recent_fixes import (
    DigitalFundWithdrawalTests,
    LogoutRedirectTests,
    CashTransferFormRecipientTests,
)
from apps.sales.test_shifts import ShiftProcessTests
from apps.payments.test_ecash_ledger import AccountantECashPaymentTests
from apps.accounting.tests.test_cashier_bank_transfer import CashierBankTransferTests

class RollbackTestResult(unittest.TextTestResult):
    def startTest(self, test):
        # Start transaction block
        self.transaction_context = transaction.atomic()
        self.transaction_context.__enter__()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        # Force rollback at the end of every test
        transaction.set_rollback(True)
        self.transaction_context.__exit__(None, None, None)


class RollbackTestRunner(unittest.TextTestRunner):
    def _makeResult(self):
        return RollbackTestResult(self.stream, self.descriptions, self.verbosity)


def main():
    print("=" * 70)
    print("RUNNING COMPREHENSIVE WORKFLOW INTEGRATION TESTS (WITH TRANSACTION ROLLBACK)")
    print("=" * 70)

    # --- Suite 1: Core Workflow Integration Tests ---
    print("\n[1/4] Core Workflow Integration Tests")
    suite1 = unittest.TestLoader().loadTestsFromTestCase(WorkflowIntegrationTests)

    # --- Suite 2: Pricing Control Mode Tests ---
    print("[2/4] Pricing Control Mode Tests")
    suite2 = unittest.TestLoader().loadTestsFromTestCase(PricingControlModeTests)

    # --- Suite 3: Digital Fund Withdrawal Balance Tests ---
    print("[3/4] Digital Fund Withdrawal Balance Tests (ECash/Momo)")
    suite3 = unittest.TestLoader().loadTestsFromTestCase(DigitalFundWithdrawalTests)

    # --- Suite 4: Recent Fixes Tests ---
    print("[4/4] Recent Fixes Tests (Logout redirect, transfer form, bank exit)")
    suite4a = unittest.TestLoader().loadTestsFromTestCase(LogoutRedirectTests)
    suite4b = unittest.TestLoader().loadTestsFromTestCase(CashTransferFormRecipientTests)

    # --- Suite 5: Shift Process Tests ---
    print("[5/6] Shift Process Tests")
    suite5 = unittest.TestLoader().loadTestsFromTestCase(ShiftProcessTests)

    # --- Suite 6: E-Cash Ledger Tests ---
    print("[6/7] E-Cash Ledger Tests")
    suite6 = unittest.TestLoader().loadTestsFromTestCase(AccountantECashPaymentTests)
    
    # --- Suite 7: Cashier Bank Transfer Tests ---
    print("[7/7] Cashier Bank Transfer Tests")
    suite7 = unittest.TestLoader().loadTestsFromTestCase(CashierBankTransferTests)

    suite = unittest.TestSuite([suite1, suite2, suite3, suite4a, suite4b, suite5, suite6, suite7])

    runner = RollbackTestRunner(verbosity=2)
    result = runner.run(suite)

    print("=" * 70)
    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)

    if result.wasSuccessful():
        print(f"[OK] SUCCESS: All {total} workflow integration tests passed!")
        print("   Database state has been completely rolled back.")
        sys.exit(0)
    else:
        print(f"[FAIL] FAILURE: {failures} failure(s) and {errors} error(s) out of {total} tests.")
        sys.exit(1)


if __name__ == "__main__":
    main()
