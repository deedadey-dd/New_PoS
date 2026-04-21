from django.db import migrations


DEFAULT_CATEGORIES = ['Transportation', 'Utilities', 'Stationery', 'Others']


def seed_expenditure_categories(apps, schema_editor):
    Tenant = apps.get_model('core', 'Tenant')
    ExpenditureCategory = apps.get_model('accounting', 'ExpenditureCategory')

    for tenant in Tenant.objects.all():
        for cat_name in DEFAULT_CATEGORIES:
            ExpenditureCategory.objects.get_or_create(
                tenant=tenant,
                name=cat_name,
                defaults={'is_default': True, 'is_active': True}
            )


def reverse_seed(apps, schema_editor):
    ExpenditureCategory = apps.get_model('accounting', 'ExpenditureCategory')
    ExpenditureCategory.objects.filter(is_default=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0003_expenditure_category'),
        ('core', '0013_tenant_allow_shop_to_shop_transfers'),
    ]

    operations = [
        migrations.RunPython(seed_expenditure_categories, reverse_code=reverse_seed),
    ]
