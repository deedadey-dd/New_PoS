"""
Views for the notifications app.
"""
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
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
def mark_as_read(request, pk):
    """Mark a single notification as read."""
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.mark_as_read()
    
    # Redirect to the related object if available
    if notification.reference_type == 'Transfer' and notification.reference_id:
        return redirect('transfers:transfer_detail', pk=notification.reference_id)
    
    return redirect('notifications:notification_list')
