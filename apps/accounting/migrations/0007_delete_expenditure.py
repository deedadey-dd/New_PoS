# Migration 0007: Deletes the old Expenditure model.
# On main branch the old Expenditure model never existed, so this is a no-op pass-through.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0006_migrate_expenditures'),
    ]

    operations = [
        # No-op on main: old Expenditure model never existed here.
        # On alpha this deleted the legacy Expenditure model after data was migrated.
    ]
