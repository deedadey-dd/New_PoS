"""
Central notification and bulletin dispatch service.
Ensures that all key business requests and activities (Refund Requests, Stock Requests,
Transfers, Adjustments, Expenditures, and Cash Transfers) create corresponding
in-app Notifications for the roles involved as well as targeted Bulletin Board posts.
"""
import logging
from typing import List, Optional, Set
from django.db import models

from apps.core.models import User, Role, Location
from apps.notifications.models import Notification, BulletinPost

logger = logging.getLogger(__name__)


def notify_and_bulletin(
    tenant,
    title: str,
    message: str,
    notification_type: str = 'SYSTEM',
    target_roles: Optional[List[str]] = None,
    target_users: Optional[List[User]] = None,
    target_locations: Optional[List[Location]] = None,
    reference_type: str = '',
    reference_id: Optional[int] = None,
    created_by: Optional[User] = None,
    is_pinned: bool = False,
    post_type: str = 'ANNOUNCEMENT',
    exclude_users: Optional[List[User]] = None,
    create_bulletin: bool = True
):
    """
    Creates both individual in-app Notifications for target users and a targeted BulletinPost.

    Args:
        tenant: The Tenant instance.
        title: Short title for notification and bulletin.
        message: Detailed body message.
        notification_type: Type choice from Notification.TYPE_CHOICES.
        target_roles: List of role names to notify (e.g. ['ACCOUNTANT', 'ADMIN', 'SHOP_MANAGER']).
        target_users: Specific User instances to notify directly.
        target_locations: List of Location instances to associate.
        reference_type: Model name string (e.g. 'RefundRequest', 'StockRequest').
        reference_id: PK of the referenced object.
        created_by: User who performed the action.
        is_pinned: Whether to pin the bulletin announcement.
        post_type: Choice from BulletinPost.POST_TYPES ('ANNOUNCEMENT', 'GENERAL', etc.).
        exclude_users: Users to exclude from individual notification pings (e.g. author).
        create_bulletin: Whether to create a BulletinPost.
    """
    if not tenant:
        return set(), None

    recipients: Set[User] = set()

    try:
        # 1. Resolve recipients
        if target_users:
            for u in target_users:
                if u and getattr(u, 'is_active', False) and getattr(u, 'tenant_id', None) == tenant.id:
                    recipients.add(u)

        if target_roles:
            user_qs = User.objects.filter(
                tenant=tenant,
                is_active=True,
                role__name__in=target_roles
            )
            # If target_locations is specified, allow users at those locations
            # plus HQ roles (Admin, Accountant, Auditor) who might not have a location assigned
            if target_locations:
                loc_pks = [loc.pk if hasattr(loc, 'pk') else loc for loc in target_locations if loc]
                user_qs = user_qs.filter(
                    models.Q(location_id__in=loc_pks) |
                    models.Q(location__isnull=True) |
                    models.Q(role__name__in=['ADMIN', 'ACCOUNTANT', 'AUDITOR', 'STORES_MANAGER'])
                )
            for u in user_qs:
                recipients.add(u)

        # Exclude users who shouldn't receive a self-notification
        if exclude_users:
            exclude_ids = {u.id for u in exclude_users if u and hasattr(u, 'id')}
            recipients = {u for u in recipients if u.id not in exclude_ids}

        # Create notifications
        for u in recipients:
            try:
                Notification.objects.create(
                    tenant=tenant,
                    user=u,
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    reference_type=reference_type,
                    reference_id=reference_id
                )
            except Exception as e:
                logger.warning("Could not create notification for user %s: %s", u, e)

        # 2. Create BulletinPost
        bulletin = None
        if create_bulletin:
            try:
                bulletin = BulletinPost.objects.create(
                    tenant=tenant,
                    created_by=created_by,
                    title=title,
                    body=message,
                    post_type=post_type,
                    notification_type=notification_type,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    is_active=True,
                    is_pinned=is_pinned
                )
                if target_roles:
                    roles = Role.objects.filter(name__in=target_roles)
                    bulletin.target_roles.set(roles)
                if target_locations:
                    bulletin.target_locations.set(target_locations)
            except Exception as e:
                logger.warning("Could not create bulletin post: %s", e)

        return recipients, bulletin
    except Exception as e:
        logger.exception("Error in notify_and_bulletin: %s", e)
        return set(), None

