from urllib.parse import urljoin

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError
from django.urls import reverse

from core.schedule_utils import is_working_day_for_user
from core.models import UserPreference

from .notification_utils import (
    unread_direct_conversation_summaries,
    unread_direct_message_count,
    unread_request_message_count,
    unread_request_notification_groups,
)


def _grafana_menu_url() -> str:
    base = (getattr(settings, "GRAFANA_PUBLIC_URL", "") or "").strip().rstrip("/")
    if not base:
        return ""
    path = (getattr(settings, "GRAFANA_MENU_PATH", "/") or "/").strip()
    if not path.startswith("/"):
        path = "/" + path
    return urljoin(base + "/", path.lstrip("/"))


def user_preferences(request):
    preference = None
    if request.user.is_authenticated:
        try:
            preference, _ = UserPreference.objects.get_or_create(user=request.user)
        except (ProgrammingError, OperationalError):
            # The site should still load while the preferences migration is pending.
            preference = None

    return {
        "ui_preferences": preference,
        "preferred_datetime_format": preference.datetime_format if preference else "d.m.Y H:i",
        "preferred_date_format": preference.date_format if preference else "d.m.Y",
        "preferred_page_size": preference.page_size if preference else 25,
        "hotkeys_enabled": preference.hotkeys_enabled if preference else True,
        "show_hotkey_legend": preference.show_hotkey_legend if preference else True,
        "grafana_public_url": getattr(settings, "GRAFANA_PUBLIC_URL", "") or "",
        "grafana_menu_url": _grafana_menu_url(),
        "is_off_duty_today": (
            request.user.is_authenticated and not is_working_day_for_user(request.user)
        ),
    }


def notification_counts(request):
    """Счётчики для колокольчика в шапке."""
    if not request.user.is_authenticated:
        return {
            "notification_dm_unread": 0,
            "notification_request_unread": 0,
            "notification_total_unread": 0,
        }
    try:
        dm = unread_direct_message_count(request.user)
        rq = unread_request_message_count(request.user)
        dm_conversations = unread_direct_conversation_summaries(request.user, conversation_limit=6)
        request_groups = unread_request_notification_groups(request.user, message_limit=18)[:6]
    except (ProgrammingError, OperationalError):
        return {
            "notification_dm_unread": 0,
            "notification_request_unread": 0,
            "notification_total_unread": 0,
            "notification_menu_items": [],
        }
    menu_items = []
    for row in dm_conversations:
        menu_items.append(
            {
                "kind": "dm",
                "title": f"Чат: {row['user'].username}",
                "preview": row["last_message"] or "(без текста)",
                "created_at": row["last_message_at"],
                "url": f"{reverse('direct_messages')}?user={row['user'].pk}",
                "count": row["unread_count"],
            }
        )
    for row in request_groups:
        latest = row["latest"]
        menu_items.append(
            {
                "kind": "request",
                "title": f"Заявка #{row['request'].pk}",
                "preview": f"{latest.author.username}: {latest.body or '(без текста)'}",
                "created_at": latest.created_at,
                "url": reverse("request_detail", kwargs={"request_id": row["request"].pk}),
                "count": row["count"],
            }
        )
    menu_items.sort(key=lambda x: x["created_at"], reverse=True)
    menu_items = menu_items[:8]
    return {
        "notification_dm_unread": dm,
        "notification_request_unread": rq,
        "notification_total_unread": dm + rq,
        "notification_menu_items": menu_items,
    }
