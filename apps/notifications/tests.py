from django.test import TestCase, Client
from django.urls import reverse
from apps.core.models import Tenant, User, Role, Location
from apps.notifications.models import BulletinPost, BulletinRead

class BulletinBoardTests(TestCase):
    def setUp(self):
        self.client = Client()
        
        self.tenant = Tenant.objects.create(
            name="Test Tenant",
            is_active=True
        )
        
        self.admin_role, _ = Role.objects.get_or_create(name="ADMIN", defaults={"description": "Admin"})
        self.manager_role, _ = Role.objects.get_or_create(name="SHOP_MANAGER", defaults={"description": "Manager"})
        self.attendant_role, _ = Role.objects.get_or_create(name="SHOP_ATTENDANT", defaults={"description": "Attendant"})
        
        self.location1 = Location.objects.create(
            tenant=self.tenant, name="Shop 1", location_type="SHOP", is_active=True
        )
        self.location2 = Location.objects.create(
            tenant=self.tenant, name="Shop 2", location_type="SHOP", is_active=True
        )
        
        self.admin_user = User.objects.create_user(
            email="admin@test.com", password="password", tenant=self.tenant, role=self.admin_role
        )
        self.manager_user = User.objects.create_user(
            email="manager@test.com", password="password", tenant=self.tenant, role=self.manager_role, location=self.location1
        )
        self.attendant_user = User.objects.create_user(
            email="attendant@test.com", password="password", tenant=self.tenant, role=self.attendant_role, location=self.location1
        )
        self.other_attendant = User.objects.create_user(
            email="other@test.com", password="password", tenant=self.tenant, role=self.attendant_role, location=self.location2
        )
        
    def test_bulletin_post_creation_and_visibility(self):
        # Admin creates a post for ALL
        post1 = BulletinPost.objects.create(
            tenant=self.tenant,
            created_by=self.admin_user,
            title="Global Post",
            body="Hello everyone"
        )
        
        # Admin creates a post for SHOP_ATTENDANTs in Shop 1
        post2 = BulletinPost.objects.create(
            tenant=self.tenant,
            created_by=self.admin_user,
            title="Shop 1 Attendants",
            body="Hello shop 1"
        )
        post2.target_roles.add(self.attendant_role)
        post2.target_locations.add(self.location1)
        
        # Test Manager visibility
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse('notifications:bulletin_board'))
        self.assertEqual(response.status_code, 200)
        posts = response.context['posts']
        self.assertIn(post1, posts)
        self.assertNotIn(post2, posts)  # Manager is not SHOP_ATTENDANT
        
        # Test Attendant 1 visibility
        self.client.force_login(self.attendant_user)
        response = self.client.get(reverse('notifications:bulletin_board'))
        self.assertEqual(response.status_code, 200)
        posts = response.context['posts']
        self.assertIn(post1, posts)
        self.assertIn(post2, posts)
        
        # Test Other Attendant visibility
        self.client.force_login(self.other_attendant)
        response = self.client.get(reverse('notifications:bulletin_board'))
        self.assertEqual(response.status_code, 200)
        posts = response.context['posts']
        self.assertIn(post1, posts)
        self.assertNotIn(post2, posts) # Wrong location
        
    def test_mark_as_read(self):
        post = BulletinPost.objects.create(
            tenant=self.tenant,
            created_by=self.admin_user,
            title="Read test",
            body="Read this"
        )
        
        self.client.force_login(self.manager_user)
        
        # Check unread
        response = self.client.get(reverse('notifications:bulletin_board'))
        self.assertNotIn(post.id, response.context['read_post_ids'])
        
        # Mark read
        self.client.get(reverse('notifications:bulletin_mark_read', args=[post.id]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        # Check read
        response = self.client.get(reverse('notifications:bulletin_board'))
        self.assertIn(post.id, response.context['read_post_ids'])

    def test_bulletin_pin_toggle(self):
        post = BulletinPost.objects.create(
            tenant=self.tenant,
            created_by=self.admin_user,
            title="Pin me",
            body="I need to be pinned"
        )
        self.client.force_login(self.admin_user)
        
        # Pin it
        url = reverse('notifications:bulletin_toggle_pin', args=[post.id])
        response = self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        
        post.refresh_from_db()
        self.assertTrue(post.is_pinned)
        
        # Unpin it
        response = self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        
        post.refresh_from_db()
        self.assertFalse(post.is_pinned)

    def test_bulletin_repost_initial_data(self):
        post = BulletinPost.objects.create(
            tenant=self.tenant,
            created_by=self.admin_user,
            title="Repost me",
            body="I need to be reposted"
        )
        post.target_locations.add(self.location1)
        
        self.client.force_login(self.admin_user)
        url = reverse('notifications:bulletin_post_create') + f'?repost={post.id}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        # Check if the initial data is properly populated in the form context
        form = response.context['form']
        self.assertEqual(form.initial.get('title'), post.title)
        self.assertEqual(form.initial.get('body'), post.body)
        self.assertIn(self.location1, form.initial.get('target_locations', []))
