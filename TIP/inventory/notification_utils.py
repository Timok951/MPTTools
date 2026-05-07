"""Подсчёт непрочитанных уведомлений (личные сообщения и переписка по заявкам)."""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone

from core.models import DirectMessage
from operations.models import EquipmentRequestMessage, EquipmentRequestThreadRead


def user_is_request_notification_recipient(user: User, eq_request) -> bool:
    if not user.is_authenticated:
        return False
    return eq_request.requester_id == user.pk or eq_request.processed_by_id == user.pk


def mark_equipment_request_thread_read(user: User, eq_request) -> None:
    """Обновить отметку «прочитано» для заявителя или обработчика заявки."""
    if not user_is_request_notification_recipient(user, eq_request):
        return
    EquipmentRequestThreadRead.objects.update_or_create(
        user=user,
        equipment_request=eq_request,
        defaults={"last_read_at": timezone.now()},
    )


def unread_direct_message_count(user: User) -> int:
    if not user.is_authenticated:
        return 0
    return DirectMessage.objects.filter(recipient=user, read_at__isnull=True).count()


def unread_direct_conversation_summaries(user: User, *, conversation_limit: int = 12):
    """Непрочитанные личные диалоги (по собеседнику)."""
    if not user.is_authenticated:
        return []
    message_qs = (
        DirectMessage.objects.filter(Q(sender=user) | Q(recipient=user))
        .select_related("sender", "recipient")
        .order_by("-created_at", "-id")
    )
    summaries: dict[int, dict] = {}
    for item in message_qs:
        counterpart = item.recipient if item.sender_id == user.pk else item.sender
        summary = summaries.setdefault(
            counterpart.pk,
            {
                "user": counterpart,
                "last_message": item.body,
                "last_message_at": item.created_at,
                "unread_count": 0,
            },
        )
        if item.recipient_id == user.pk and item.read_at is None:
            summary["unread_count"] += 1
    unread = [row for row in summaries.values() if row["unread_count"] > 0]
    unread.sort(key=lambda row: row["last_message_at"], reverse=True)
    return unread[:conversation_limit]


def _request_message_unread_qs(user: User):
    lr_sq = EquipmentRequestThreadRead.objects.filter(
        user=user,
        equipment_request_id=OuterRef("request_id"),
    ).values("last_read_at")[:1]
    return (
        EquipmentRequestMessage.objects.filter(
            Q(request__requester=user) | Q(request__processed_by=user)
        )
        .exclude(author=user)
        .annotate(lr=Subquery(lr_sq))
        .filter(Q(lr__isnull=True) | Q(created_at__gt=F("lr")))
    )


def unread_request_message_count(user: User) -> int:
    if not user.is_authenticated:
        return 0
    return _request_message_unread_qs(user).count()


def unread_request_notification_groups(user: User, message_limit: int = 80):
    """Заявки с непрочитанными сообщениями (агрегация для страницы уведомлений)."""
    if not user.is_authenticated:
        return []
    qs = (
        _request_message_unread_qs(user)
        .select_related("request", "author")
        .order_by("-created_at")[:message_limit]
    )
    groups: dict[int, dict] = {}
    for m in qs:
        rid = m.request_id
        if rid not in groups:
            groups[rid] = {"request": m.request, "latest": m, "count": 0}
        groups[rid]["count"] += 1
    return sorted(groups.values(), key=lambda x: x["latest"].created_at, reverse=True)
