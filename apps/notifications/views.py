"""
Views for the notifications app.
"""
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.decorators.http import require_POST
from django.views.generic import ListView
from django.contrib import messages

from .models import Notification


class NotificationListView(LoginRequiredMixin, ListView):
    """List all notifications for the current user."""
    model = Notification
    template_name = 'notifications/notification_list.html'
    context_object_name = 'notifications'
    paginate_by = 20
    
    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)
    
    def get(self, request, *args, **kwargs):
        # Check if we need to mark a notification as read
        mark_read = request.GET.get('mark_read')
        if mark_read:
            try:
                notification = Notification.objects.get(pk=mark_read, user=request.user)
                notification.mark_as_read()
            except Notification.DoesNotExist:
                pass
        return super().get(request, *args, **kwargs)


@login_required
@require_POST
def mark_all_read(request):
    """Mark all notifications as read for the current user."""
    Notification.objects.filter(user=request.user, is_read=False).update(
        is_read=True
    )
    messages.success(request, 'All notifications marked as read.')
    
    # Redirect to referrer or dashboard
    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('core:dashboard')


@login_required
@require_POST
def mark_as_read(request, pk):
    """Mark a single notification as read."""
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.mark_as_read()
    
    # Redirect to the related object if available
    if notification.reference_type == 'Transfer' and notification.reference_id:
        return redirect('transfers:transfer_detail', pk=notification.reference_id)
    
    return redirect('notifications:notification_list')


@login_required
@require_POST
def mark_notification_read_api(request, pk):
    """AJAX endpoint to mark a single notification as read."""
    from django.http import JsonResponse
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.mark_as_read()
    return JsonResponse({
        'status': 'success', 
        'unread_count': Notification.get_unread_count(request.user)
    })

from django.urls import reverse_lazy
from django.views.generic.edit import CreateView, DeleteView
from django.db.models import Q
from django.http import JsonResponse
from .models import BulletinPost, BulletinRead

class BulletinBoardView(LoginRequiredMixin, ListView):
    model = BulletinPost
    template_name = 'notifications/bulletin_board.html'
    context_object_name = 'posts'
    paginate_by = 15

    def get_template_names(self):
        """Use the superadmin-themed template for SUPER_ADMIN and TENANT_MANAGER roles."""
        user = self.request.user
        role_name = user.role.name if user.role else None
        if role_name in ['SUPER_ADMIN', 'TENANT_MANAGER'] or user.is_superuser:
            return ['notifications/bulletin_board_superadmin.html']
        return [self.template_name]
    
    def get_queryset(self):
        user = self.request.user
        tenant = user.tenant
        
        # Base query for active posts in this tenant
        qs = BulletinPost.objects.filter(tenant=tenant, is_active=True)
        
        role_name = user.role.name if user.role else None
        
        # SUPER_ADMIN, TENANT_MANAGER, and ADMIN see everything
        if role_name in ['ADMIN', 'SUPER_ADMIN', 'TENANT_MANAGER'] or user.is_superuser:
            return qs.order_by('-created_at')

        # Role-based filtering
        role_filter = Q(target_roles__isnull=True)
        if user.role:
            role_filter |= Q(target_roles=user.role)
            
        # Location-based filtering
        loc_filter = Q(target_locations__isnull=True)
        if user.location:
            loc_filter |= Q(target_locations=user.location)

        return qs.filter(role_filter, loc_filter).distinct().order_by('-created_at')
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Get list of post IDs the user has read
        read_post_ids = BulletinRead.objects.filter(user=user).values_list('bulletin_post_id', flat=True)
        context['read_post_ids'] = list(read_post_ids)
        
        # Add permissions context
        can_post = False
        if user.is_superuser:
            can_post = True
        elif user.role:
            if user.role.name in ['ADMIN', 'SUPER_ADMIN', 'TENANT_MANAGER', 'ACCOUNTANT', 'STORES_MANAGER', 'SHOP_MANAGER']:
                can_post = True
        context['can_post'] = can_post
        
        # Add post form if user can post
        if can_post:
            from .forms import BulletinPostForm
            context['form'] = BulletinPostForm(user=user)
            
            role_name = user.role.name if user.role else None
            is_broadcaster = role_name in ['SUPER_ADMIN', 'TENANT_MANAGER'] or user.is_superuser

            if is_broadcaster:
                # Super admins broadcast one post per tenant, so deduplicate history
                # by picking one representative (highest id) per unique title
                from django.db.models import Max
                unique_broadcasts = (
                    BulletinPost.objects
                    .filter(created_by=user)
                    .values('title')
                    .annotate(latest_id=Max('id'))
                    .order_by('-latest_id')
                )
                representative_ids = [b['latest_id'] for b in unique_broadcasts]
                
                # Preserve ordering via Case/When
                from django.db.models import Case, When, IntegerField
                ordering = Case(
                    *[When(id=pk, then=pos) for pos, pk in enumerate(representative_ids)],
                    output_field=IntegerField()
                )
                user_posts = (
                    BulletinPost.objects
                    .filter(id__in=representative_ids)
                    .order_by(ordering)
                )
                context['history_pinned_posts'] = user_posts.filter(is_pinned=True)[:5]
                context['history_recent_posts'] = user_posts.filter(is_pinned=False)[:10]
            else:
                # Regular users: show their posts directly, no deduplication needed
                user_posts = BulletinPost.objects.filter(created_by=user).order_by('-created_at')
                context['history_pinned_posts'] = user_posts.filter(is_pinned=True)[:5]
                context['history_recent_posts'] = user_posts.filter(is_pinned=False)[:10]
            
        return context

class BulletinPostCreateView(LoginRequiredMixin, CreateView):
    model = BulletinPost
    template_name = 'notifications/bulletin_post_form.html'
    success_url = reverse_lazy('notifications:bulletin_board')

    def get_template_names(self):
        """Use the superadmin-themed template for SUPER_ADMIN and TENANT_MANAGER roles."""
        user = self.request.user
        role_name = user.role.name if user.role else None
        if role_name in ['SUPER_ADMIN', 'TENANT_MANAGER'] or user.is_superuser:
            return ['notifications/bulletin_post_form_superadmin.html']
        return [self.template_name]
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        role_name = user.role.name if user.role else None
        if role_name in ['SUPER_ADMIN', 'TENANT_MANAGER'] or user.is_superuser:
            from apps.core.models import FeatureMessage
            context['feature_messages'] = FeatureMessage.objects.filter(is_active=True).order_by('-created_at')
        return context
    
    def get_form_class(self):
        from .forms import BulletinPostForm
        return BulletinPostForm
        
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs
        
    def get_initial(self):
        initial = super().get_initial()
        repost_id = self.request.GET.get('repost')
        if repost_id:
            try:
                post = BulletinPost.objects.get(id=repost_id, created_by=self.request.user)
                initial['title'] = post.title
                initial['body'] = post.body
                initial['post_type'] = post.post_type
                initial['target_roles'] = post.target_roles.all()
                initial['target_locations'] = post.target_locations.all()
            except BulletinPost.DoesNotExist:
                pass
        return initial
        
    def form_valid(self, form):
        user = self.request.user
        role_name = user.role.name if user.role else None

        # Standard tenant users
        if user.tenant:
            form.instance.tenant = user.tenant
            form.instance.created_by = user
            messages.success(self.request, 'Bulletin post created successfully.')
            return super().form_valid(form)
            
        # Super Admins / Tenant Managers broadcast to their tenants
        elif role_name in ['SUPER_ADMIN', 'TENANT_MANAGER'] or user.is_superuser:
            from apps.core.models import Tenant
            if role_name == 'TENANT_MANAGER':
                from apps.subscriptions.models import TenantManagerAssignment
                tenant_ids = TenantManagerAssignment.objects.filter(manager=user).values_list('tenant_id', flat=True)
                tenants = Tenant.objects.filter(id__in=tenant_ids, is_active=True)
            else:
                tenants = Tenant.objects.filter(is_active=True)
                
            post_data = form.cleaned_data
            
            for tenant in tenants:
                post = BulletinPost.objects.create(
                    tenant=tenant,
                    created_by=user,
                    title=post_data['title'],
                    body=post_data['body'],
                    post_type=post_data['post_type']
                )
                if post_data.get('target_roles'):
                    post.target_roles.set(post_data['target_roles'])
                    
            messages.success(self.request, f'Bulletin broadcasted successfully to {tenants.count()} active tenants.')
            return redirect(self.success_url)
            
        # Fallback (shouldn't be reached ideally)
        messages.error(self.request, 'You do not have permission to create bulletin posts without a tenant context.')
        return redirect(self.success_url)

class BulletinPostDeleteView(LoginRequiredMixin, DeleteView):
    model = BulletinPost
    success_url = reverse_lazy('notifications:bulletin_board')
    
    def get_queryset(self):
        # Users can only delete their own posts, Admins can delete any
        user = self.request.user
        qs = BulletinPost.objects.filter(tenant=user.tenant)
        if user.role and user.role.name != 'ADMIN':
            qs = qs.filter(created_by=user)
        return qs
        
    def delete(self, request, *args, **kwargs):
        messages.success(request, 'Bulletin post deleted.')
        return super().delete(request, *args, **kwargs)

@login_required
@require_POST
def bulletin_mark_read(request, pk):
    post = get_object_or_404(BulletinPost, pk=pk, tenant=request.user.tenant)
    BulletinRead.objects.get_or_create(bulletin_post=post, user=request.user)
    return JsonResponse({'status': 'success'})

@login_required
@require_POST
def bulletin_toggle_pin(request, pk):
    if request.method == 'POST':
        post = get_object_or_404(BulletinPost, pk=pk, created_by=request.user)
        post.is_pinned = not post.is_pinned
        post.save(update_fields=['is_pinned'])
        return JsonResponse({'status': 'success', 'is_pinned': post.is_pinned})
    return JsonResponse({'status': 'error'}, status=400)

@login_required
@require_POST
def bulletin_mark_all_read(request):
    user = request.user
    # Get all unread posts that the user can see
    # This is a bit complex due to visibility rules, so we'll just instantiate the view's get_queryset
    view = BulletinBoardView()
    view.request = request
    qs = view.get_queryset()
    
    read_ids = BulletinRead.objects.filter(user=user).values_list('bulletin_post_id', flat=True)
    unread_posts = qs.exclude(id__in=read_ids)
    
    reads_to_create = [BulletinRead(bulletin_post=post, user=user) for post in unread_posts]
    BulletinRead.objects.bulk_create(reads_to_create, ignore_conflicts=True)
    
    messages.success(request, 'All bulletin messages marked as read.')
    return redirect('notifications:bulletin_board')
