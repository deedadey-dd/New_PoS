from django.test import TestCase
from django.urls import reverse
from apps.core.models import Tenant, Role, User
from apps.notifications.models import BulletinPost

class BulletinTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant")
        self.admin_role, _ = Role.objects.get_or_create(name='ADMIN')
        self.shop_manager_role, _ = Role.objects.get_or_create(name='SHOP_MANAGER')
        
        self.admin_user = User.objects.create_user(
            email="admin@test.com", password="password123",
            tenant=self.tenant, role=self.admin_role
        )
        
        self.shop_manager = User.objects.create_user(
            email="manager@test.com", password="password123",
            tenant=self.tenant, role=self.shop_manager_role
        )

    def test_admin_can_create_system_update(self):
        """Admin should be able to create a SYSTEM_UPDATE bulletin."""
        self.client.force_login(self.admin_user)
        
        response = self.client.post(reverse('notifications:bulletin_post_create'), {
            'title': 'New Feature',
            'body': 'We have added a new feature.',
            'post_type': 'SYSTEM_UPDATE',
        })
        
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        
        # Verify it was created
        post = BulletinPost.objects.filter(title='New Feature').first()
        self.assertIsNotNone(post)
        self.assertEqual(post.post_type, 'SYSTEM_UPDATE')
        
    def test_shop_manager_cannot_create_system_update(self):
        """Shop managers should not be allowed to post SYSTEM_UPDATE bulletins."""
        self.client.force_login(self.shop_manager)
        
        # Request the form GET
        response = self.client.get(reverse('notifications:bulletin_post_create'))
        
        # SYSTEM_UPDATE should not be in the choices rendered
        self.assertNotContains(response, 'value="SYSTEM_UPDATE"')

    def test_bulletin_board_renders_system_update(self):
        """Test that the bulletin board renders the system update distinctly."""
        BulletinPost.objects.create(
            tenant=self.tenant,
            created_by=self.admin_user,
            title='Test Update',
            body='Body content',
            post_type='SYSTEM_UPDATE'
        )
        
        self.client.force_login(self.shop_manager)
        response = self.client.get(reverse('notifications:bulletin_board'))
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Update')
        self.assertContains(response, 'bi-shield-check') # The icon for system update
