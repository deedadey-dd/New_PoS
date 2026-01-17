# Generated manually for shop-level e-cash tracking

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('payments', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ecashledger',
            name='shop',
            field=models.ForeignKey(
                blank=True,
                help_text='Shop where this e-cash transaction occurred',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ecash_transactions',
                to='core.location'
            ),
        ),
        migrations.AddField(
            model_name='ecashwithdrawal',
            name='shop',
            field=models.ForeignKey(
                blank=True,
                help_text='Shop to withdraw e-cash from (leave blank for tenant-wide)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ecash_withdrawals',
                to='core.location'
            ),
        ),
    ]
