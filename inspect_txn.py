import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pos_system.settings")
django.setup()

from apps.customers.models import CustomerTransaction

print("--- CREDITS ---")
for txn in CustomerTransaction.objects.filter(transaction_type='CREDIT').order_by('-created_at')[:20]:
    print(f"ID: {txn.pk}, Amt: {txn.amount}, Desc: {txn.description}")
