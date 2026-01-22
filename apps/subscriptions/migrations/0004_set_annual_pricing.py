"""
Data migration to add annual pricing to existing subscription plans.
Annual pricing offers a discount for paying annually:
- Starter: GH₵250 → GH₵220/month (12% savings)
- Standard: GH₵350 → GH₵300/month (14% savings)
- Premium additional shop: GH₵100 → GH₵85/month (15% savings)
"""
from django.db import migrations
from decimal import Decimal


def add_annual_pricing(apps, schema_editor):
    SubscriptionPlan = apps.get_model('subscriptions', 'SubscriptionPlan')
    
    # Update Starter plan
    try:
        starter = SubscriptionPlan.objects.get(code='STARTER')
        starter.annual_base_price = Decimal('220.00')
        starter.save()
    except SubscriptionPlan.DoesNotExist:
        pass
    
    # Update Standard plan
    try:
        standard = SubscriptionPlan.objects.get(code='STANDARD')
        standard.annual_base_price = Decimal('300.00')
        standard.save()
    except SubscriptionPlan.DoesNotExist:
        pass
    
    # Update Premium plan
    try:
        premium = SubscriptionPlan.objects.get(code='PREMIUM')
        premium.annual_base_price = Decimal('300.00')  # Same base as standard
        premium.annual_additional_shop_price = Decimal('85.00')  # Discounted from 100
        premium.save()
    except SubscriptionPlan.DoesNotExist:
        pass


def reverse_annual_pricing(apps, schema_editor):
    SubscriptionPlan = apps.get_model('subscriptions', 'SubscriptionPlan')
    SubscriptionPlan.objects.all().update(
        annual_base_price=None,
        annual_additional_shop_price=None
    )


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0003_add_annual_pricing'),
    ]

    operations = [
        migrations.RunPython(add_annual_pricing, reverse_annual_pricing),
    ]
