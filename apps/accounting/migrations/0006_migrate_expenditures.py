from django.db import migrations, models
from django.utils import timezone
from decimal import Decimal

def migrate_expenditures(apps, schema_editor):
    Expenditure = apps.get_model('accounting', 'Expenditure')
    ExpenditureRequest = apps.get_model('accounting', 'ExpenditureRequest')
    ExpenditureItem = apps.get_model('accounting', 'ExpenditureItem')

    # Status mapping
    # Request status: PENDING, PARTIAL, FULLY_APPROVED, REJECTED
    # Item status: PENDING, APPROVED, REJECTED
    
    status_map_req = {
        'PENDING': 'PENDING',
        'APPROVED': 'FULLY_APPROVED',
        'REJECTED': 'REJECTED',
    }
    
    status_map_item = {
        'PENDING': 'PENDING',
        'APPROVED': 'APPROVED',
        'REJECTED': 'REJECTED',
    }

    for exp in Expenditure.objects.all():
        # Create Request (Parent)
        # We manually generate voucher number here to avoid dependency on model save() methods during migrations
        date_str = exp.created_at.strftime('%Y%m%d')
        # Simple suffix per original expenditure ID to ensure uniqueness in migration
        voucher_number = f"EXP-{date_str}-{exp.id:04d}"
        
        req = ExpenditureRequest.objects.create(
            tenant=exp.tenant,
            voucher_number=voucher_number,
            location=exp.location,
            requested_by=exp.requested_by,
            status=status_map_req.get(exp.status, 'PENDING'),
            created_at=exp.created_at
        )
        
        # Create Item (Child)
        ExpenditureItem.objects.create(
            tenant=exp.tenant,
            request=req,
            category=exp.category,
            amount=exp.amount,
            description=exp.reason,
            status=status_map_item.get(exp.status, 'PENDING'),
            rejection_reason=exp.rejection_reason,
            approved_by=exp.approved_by,
            approved_at=exp.approved_at
        )

def rollback_expenditures(apps, schema_editor):
    # In rollback, we'd lose the voucher grouping but usually we don't rollback data migrations in Prod
    # For now, let's just clear the new models
    ExpenditureRequest = apps.get_model('accounting', 'ExpenditureRequest')
    ExpenditureItem = apps.get_model('accounting', 'ExpenditureItem')
    ExpenditureItem.objects.all().delete()
    ExpenditureRequest.objects.all().delete()

class Migration(migrations.Migration):
    dependencies = [
        ('accounting', '0005_expenditurerequest_expenditureitem'),
    ]

    operations = [
        migrations.RunPython(migrate_expenditures, rollback_expenditures),
    ]
