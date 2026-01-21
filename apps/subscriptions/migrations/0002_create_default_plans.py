"""
Data migration to create default subscription plans.
"""
from django.db import migrations


def create_default_plans(apps, schema_editor):
    """Create the default subscription plans."""
    SubscriptionPlan = apps.get_model('subscriptions', 'SubscriptionPlan')
    
    # Starter Plan
    SubscriptionPlan.objects.create(
        name='Starter',
        code='STARTER',
        description='Perfect for small businesses just getting started',
        base_price=250.00,
        max_shops=2,
        additional_shop_price=0.00,
        features=[
            'Unlimited products',
            'Unlimited transactions',
            'Real-time inventory tracking',
            'Sales reports & analytics',
            'Customer management',
        ],
        is_active=True,
        display_order=1
    )
    
    # Standard Plan
    SubscriptionPlan.objects.create(
        name='Standard',
        code='STANDARD',
        description='Ideal for growing businesses with multiple locations',
        base_price=350.00,
        max_shops=5,
        additional_shop_price=0.00,
        features=[
            'Unlimited products',
            'Unlimited transactions',
            'Real-time inventory tracking',
            'Sales reports & analytics',
            'Customer management',
            'Product transfers between locations',
            'Multi-user support',
        ],
        is_active=True,
        display_order=2
    )
    
    # Premium Plan
    SubscriptionPlan.objects.create(
        name='Premium',
        code='PREMIUM',
        description='For large enterprises with unlimited growth potential',
        base_price=350.00,
        max_shops=5,
        additional_shop_price=100.00,
        features=[
            'Everything in Standard',
            'Additional shops at GH₵100/month each',
            'Priority support',
            'Advanced reporting',
            'Dedicated account manager',
        ],
        is_active=True,
        display_order=3
    )


def remove_default_plans(apps, schema_editor):
    """Remove the default subscription plans."""
    SubscriptionPlan = apps.get_model('subscriptions', 'SubscriptionPlan')
    SubscriptionPlan.objects.filter(code__in=['STARTER', 'STANDARD', 'PREMIUM']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_default_plans, remove_default_plans),
    ]
