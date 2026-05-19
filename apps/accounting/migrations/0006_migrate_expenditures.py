# Migration 0006: Data migration from old Expenditure model to ExpenditureRequest/ExpenditureItem.
# On main branch the old Expenditure model never existed, so this is a no-op pass-through.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0005_expenditurerequest_expenditureitem'),
    ]

    operations = [
        # No-op on main: old Expenditure model never existed here.
        # On alpha this migrated data from Expenditure → ExpenditureRequest/ExpenditureItem.
    ]
