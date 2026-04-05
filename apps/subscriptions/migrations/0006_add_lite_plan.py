"""
Data migration to add the Lite subscription plan.
"""
from django.db import migrations


def add_lite_plan(apps, schema_editor):
    """Create the Lite subscription plan."""
    SubscriptionPlan = apps.get_model('subscriptions', 'SubscriptionPlan')
    
    # Lite Plan
    SubscriptionPlan.objects.create(
        name='Lite',
        code='LITE',
        description='Perfect for individual shop owners with a single location',
        base_price=50.00,
        annual_base_price=50.00,
        max_shops=1,
        additional_shop_price=0.00,
        features=[
            'Single Shop',
            'Admin Control',
            'Inventory Transfers',
            'Standard Roles (Admin, Manager, Attendant)',
            'Unlimited products',
            'Unlimited transactions',
        ],
        is_active=True,
        display_order=0  # Set as first plan
    )


def remove_lite_plan(apps, schema_editor):
    """Remove the Lite subscription plan."""
    SubscriptionPlan = apps.get_model('subscriptions', 'SubscriptionPlan')
    SubscriptionPlan.objects.filter(code='LITE').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0005_add_shops_paid_field'),
    ]

    operations = [
        migrations.RunPython(add_lite_plan, remove_lite_plan),
    ]
