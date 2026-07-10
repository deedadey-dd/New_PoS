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
from apps.accounting.tests.test_digital_confirmation import DigitalConfirmationPendingDispatchTests
from apps.sales.test_partial_dispatch import PartialDispatchTests

# New security & regression test suites
from apps.sales.test_security_and_perf import (
    AnonymousUserGuardTests,
    RequirePostEnforcementTests,
    TenantIsolationTests,
    SaleVoidInventoryTests,
    CashierWorkflowTests,
    InventoryLedgerConsistencyTests,
    CreditLimitEnforcementTests,
)

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
    print("\n[1/11] Core Workflow Integration Tests")
    suite1 = unittest.TestLoader().loadTestsFromTestCase(WorkflowIntegrationTests)

    # --- Suite 2: Pricing Control Mode Tests ---
    print("[2/11] Pricing Control Mode Tests")
    suite2 = unittest.TestLoader().loadTestsFromTestCase(PricingControlModeTests)

    # --- Suite 3: Digital Fund Withdrawal Balance Tests ---
    print("[3/11] Digital Fund Withdrawal Balance Tests (ECash/Momo)")
    suite3 = unittest.TestLoader().loadTestsFromTestCase(DigitalFundWithdrawalTests)

    # --- Suite 4: Recent Fixes Tests ---
    print("[4/11] Recent Fixes Tests (Logout, transfer form, bank exit, bulk receive, momo history, bulletins)")
    suite4a = unittest.TestLoader().loadTestsFromTestCase(LogoutRedirectTests)
    suite4b = unittest.TestLoader().loadTestsFromTestCase(CashTransferFormRecipientTests)

    from apps.inventory.tests.test_bulk_receive import BulkReceiveTests
    from apps.inventory.tests.test_batch_edit import BatchEditAccountabilityTests
    from apps.accounting.tests.test_momo_history import MomoHistoryTests
    from apps.notifications.tests.test_bulletins import BulletinTests

    suite4c = unittest.TestLoader().loadTestsFromTestCase(BulkReceiveTests)
    suite4_batch = unittest.TestLoader().loadTestsFromTestCase(BatchEditAccountabilityTests)
    suite4d = unittest.TestLoader().loadTestsFromTestCase(MomoHistoryTests)
    suite4e = unittest.TestLoader().loadTestsFromTestCase(BulletinTests)

    # --- Suite 5: Shift Process Tests ---
    print("[5/11] Shift Process Tests")
    suite5 = unittest.TestLoader().loadTestsFromTestCase(ShiftProcessTests)

    # --- Suite 6: E-Cash Ledger Tests ---
    print("[6/11] E-Cash Ledger Tests")
    suite6 = unittest.TestLoader().loadTestsFromTestCase(AccountantECashPaymentTests)

    # --- Suite 7: Cashier Bank Transfer Tests ---
    print("[7/11] Cashier Bank Transfer Tests")
    suite7 = unittest.TestLoader().loadTestsFromTestCase(CashierBankTransferTests)

    # --- Suite 8: Anonymous User Guard Tests ---
    print("[8/11] Anonymous User Guard Tests (login redirect for all apps)")
    suite8 = unittest.TestLoader().loadTestsFromTestCase(AnonymousUserGuardTests)

    # --- Suite 9: require_POST Enforcement Tests ---
    print("[9/11] require_POST Enforcement Tests (GET -> 405 on mutating endpoints)")
    suite9 = unittest.TestLoader().loadTestsFromTestCase(RequirePostEnforcementTests)

    # --- Suite 10: Tenant Isolation / IDOR Tests ---
    print("[10/11] Tenant Isolation / IDOR Protection Tests")
    suite10a = unittest.TestLoader().loadTestsFromTestCase(TenantIsolationTests)
    suite10b = unittest.TestLoader().loadTestsFromTestCase(SaleVoidInventoryTests)
    suite10c = unittest.TestLoader().loadTestsFromTestCase(CashierWorkflowTests)
    suite10d = unittest.TestLoader().loadTestsFromTestCase(InventoryLedgerConsistencyTests)

    # --- Suite 11: Credit Limit Enforcement Tests ---
    print("[11/14] Credit Limit Enforcement Tests")
    suite11 = unittest.TestLoader().loadTestsFromTestCase(CreditLimitEnforcementTests)

    # --- Suite 12: Discount Parameters Tests ---
    print("[12/14] Discount Parameters Tests")
    from apps.sales.test_discounts import DiscountParameterTests
    suite12 = unittest.TestLoader().loadTestsFromTestCase(DiscountParameterTests)

    # --- Suite 13: Digital Confirmation PENDING_DISPATCH Tests (MOMO + E-Cash) ---
    print("[13/14] Digital Confirmation PENDING_DISPATCH Tests (MOMO + E-Cash)")
    suite13 = unittest.TestLoader().loadTestsFromTestCase(DigitalConfirmationPendingDispatchTests)

    # --- Suite 14: Partial Dispatch Tests ---
    print("[14/14] Partial Dispatch Tests (strict workflow, per-item quantities, atomic rollback)")
    suite14 = unittest.TestLoader().loadTestsFromTestCase(PartialDispatchTests)

    suite = unittest.TestSuite([
        suite1, suite2, suite3,
        suite4a, suite4b, suite4c, suite4_batch, suite4d, suite4e,
        suite5, suite6, suite7,
        suite8, suite9,
        suite10a, suite10b, suite10c, suite10d,
        suite11, suite12, suite13, suite14,
    ])

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
