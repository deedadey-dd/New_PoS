from decimal import Decimal
from django.db import transaction
from apps.accounting.models import DigitalFundWithdrawal
from apps.payments.models import ECashLedger, PaymentProviderConfig
from apps.core.models import Tenant

@transaction.atomic
def backfill():
    print("Starting backfill of ECashLedger withdrawals...")
    added_withdrawals = 0
    
    tenant = Tenant.objects.first()
    if not tenant:
        print("No tenant found.")
        return
        
    default_provider = PaymentProviderConfig.objects.filter(tenant=tenant, is_active=True).first()
    
    # Process old DigitalFundWithdrawals for ECASH
    withdrawals = DigitalFundWithdrawal.objects.filter(fund_source='ECASH')
    for w in withdrawals:
        exists = ECashLedger.objects.filter(reference_type='DigitalFundWithdrawal', reference_id=w.pk).exists()
        if not exists:
            ECashLedger.objects.create(
                tenant=w.tenant,
                transaction_type='WITHDRAWAL',
                amount=-abs(w.amount),  # Negative = outgoing
                reference_type='DigitalFundWithdrawal',
                reference_id=w.pk,
                provider_config=w.provider_config or default_provider,
                provider=(w.provider_config.provider if w.provider_config else default_provider.provider) if default_provider else 'PAYSTACK',
                created_by=w.accountant,
                notes=f"Legacy E-Cash Withdrawal: {w.notes}",
                shop=w.shop
            )
            added_withdrawals += 1

    print(f"Backfill complete! Added {added_withdrawals} legacy withdrawals.")

backfill()
