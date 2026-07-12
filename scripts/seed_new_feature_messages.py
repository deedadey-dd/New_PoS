import os
import django
import sys
from pathlib import Path

# Setup Django environment
sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pos_system.settings")
django.setup()

from apps.core.models import FeatureMessage

msg1 = FeatureMessage.objects.filter(feature_key='combine_requests').first()
if not msg1:
    FeatureMessage.objects.create(
        feature_key='combine_requests',
        title='Combination of Requests',
        content='<p>You can now combine multiple pending stock requests from the same shop into a single transfer. This makes it easier to fulfill large numbers of small requests efficiently!</p>',
        is_active=True
    )
    print("Seeded 'Combination of Requests'")

msg2 = FeatureMessage.objects.filter(feature_key='change_to_account').first()
if not msg2:
    FeatureMessage.objects.create(
        feature_key='change_to_account',
        title='Pay and Send Change to Customer Account',
        content='<p>When a customer is selected for a sale, any excess payment (change) will automatically be credited to their account. If they don\'t want the change on their account, simply process the sale as a walk-in without selecting them.</p>',
        is_active=True
    )
    print("Seeded 'Pay and Send Change to Customer Account'")

print("Seeding complete.")
