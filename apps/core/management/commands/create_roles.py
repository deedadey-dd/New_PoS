"""
Management command to create initial roles with default permissions.
"""
from django.core.management.base import BaseCommand
from apps.core.models import Role


class Command(BaseCommand):
    help = 'Create initial roles with default permissions'
    
    def handle(self, *args, **options):
        roles_data = [
            {
                'name': 'SUPER_ADMIN',
                'description': 'Platform super administrator with full access',
                'can_manage_users': True,
                'can_manage_inventory': True,
                'can_manage_sales': True,
                'can_view_reports': True,
                'can_approve_refunds': True,
                'can_approve_returns': True,
                'can_manage_accounting': True,
                'can_view_audit_logs': True,
            },
            {
                'name': 'ADMIN',
                'description': 'Tenant administrator with full access to their organization',
                'can_manage_users': True,
                'can_manage_inventory': True,
                'can_manage_sales': True,
                'can_view_reports': True,
                'can_approve_refunds': True,
                'can_approve_returns': True,
                'can_manage_accounting': True,
                'can_view_audit_logs': True,
            },
            {
                'name': 'PRODUCTION_MANAGER',
                'description': 'Manages production batches and inventory',
                'can_manage_users': False,
                'can_manage_inventory': True,
                'can_manage_sales': False,
                'can_view_reports': True,
                'can_approve_refunds': False,
                'can_approve_returns': False,
                'can_manage_accounting': False,
                'can_view_audit_logs': False,
            },
            {
                'name': 'STORES_MANAGER',
                'description': 'Manages warehouse/stores inventory and transfers',
                'can_manage_users': False,
                'can_manage_inventory': True,
                'can_manage_sales': False,
                'can_view_reports': True,
                'can_approve_refunds': False,
                'can_approve_returns': True,
                'can_manage_accounting': False,
                'can_view_audit_logs': False,
            },
            {
                'name': 'SHOP_MANAGER',
                'description': 'Manages a shop and its attendants',
                'can_manage_users': False,
                'can_manage_inventory': True,
                'can_manage_sales': True,
                'can_view_reports': True,
                'can_approve_refunds': True,
                'can_approve_returns': False,
                'can_manage_accounting': False,
                'can_view_audit_logs': False,
            },
            {
                'name': 'SHOP_ATTENDANT',
                'description': 'Handles sales at a shop',
                'can_manage_users': False,
                'can_manage_inventory': False,
                'can_manage_sales': True,
                'can_view_reports': False,
                'can_approve_refunds': False,
                'can_approve_returns': False,
                'can_manage_accounting': False,
                'can_view_audit_logs': False,
            },
            {
                'name': 'ACCOUNTANT',
                'description': 'Manages accounting, remittances, and cash',
                'can_manage_users': False,
                'can_manage_inventory': False,
                'can_manage_sales': False,
                'can_view_reports': True,
                'can_approve_refunds': False,
                'can_approve_returns': False,
                'can_manage_accounting': True,
                'can_view_audit_logs': False,
            },
            {
                'name': 'AUDITOR',
                'description': 'View-only access to reports and audit logs',
                'can_manage_users': False,
                'can_manage_inventory': False,
                'can_manage_sales': False,
                'can_view_reports': True,
                'can_approve_refunds': False,
                'can_approve_returns': False,
                'can_manage_accounting': False,
                'can_view_audit_logs': True,
            },
        ]
        
        created_count = 0
        updated_count = 0
        
        for role_data in roles_data:
            role, created = Role.objects.update_or_create(
                name=role_data['name'],
                defaults=role_data
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'Created role: {role.get_name_display()}'))
            else:
                updated_count += 1
                self.stdout.write(f'Updated role: {role.get_name_display()}')
        
        self.stdout.write(self.style.SUCCESS(
            f'\nDone! Created {created_count} roles, updated {updated_count} roles.'
        ))
