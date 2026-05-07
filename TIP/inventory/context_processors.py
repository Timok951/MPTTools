from urllib.parse import urljoin

from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from core.schedule_utils import is_working_day_for_user
from core.models import UserPreference


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
