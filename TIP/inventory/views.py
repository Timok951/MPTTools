import csv
from datetime import datetime, time, timedelta
import hashlib
import io
import logging
import os
from pathlib import Path
import random
import secrets
import tempfile
import zipfile
import math
from urllib.parse import urlencode

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, String
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from smtplib import SMTPException

from django.conf import settings
from django.utils.html import escape

from core.mail_out import send_multipart_email
from core.registration_domains import get_registration_email_domains
from django.views.decorators.http import require_POST
from django.utils import translation
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.db.utils import OperationalError, ProgrammingError
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.authtoken.models import Token

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment, STATUS_IN_STOCK, STATUS_REPAIR, STATUS_RETIRED
from audit.models import AdminPortalLog, AuditLog
from core.models import (
    Cabinet,
    DirectMessage,
    EmployeeSchedule,
    EquipmentCategory,
    PasswordResetCode,
    UserPreference,
    Workplace,
    WorkplaceMember,
)
from operations.models import (
    EquipmentRequest,
    EquipmentRequestMessage,
    EquipmentRequestPhoto,
    REQUEST_KIND_RESTOCK,
    REQUEST_KIND_WRITEOFF,
    REQUEST_APPROVED,
    REQUEST_REJECTED,
    RESTOCK_NON_CONSUMABLE_INCREASE,
    RESTOCK_NON_CONSUMABLE_SET_IN_STOCK,
    MaterialUsage,
    REQUEST_PENDING,
)
from .authz import (
    GROUP_ADMIN,
    GROUP_BUILDER,
    GROUP_FIRST_LINE_SUPPORT,
    GROUP_TECHNICIAN,
    ROLE_CAPABILITY_LABELS,
    ROLE_ALIASES,
    ROLE_SPECS,
    GROUP_SENIOR_TECHNICIAN,
    GROUP_SYSADMIN,
    GROUP_WAREHOUSE,
    user_has_capability,
    user_in_group,
)
from .backup_utils import create_postgresql_backup, get_postgresql_backup_config, restore_postgresql_custom_dump
from .forms import (
    BackupImportForm,
    PostgresqlDumpImportForm,
    DirectMessageForm,
    EquipmentCheckoutForm,
    EquipmentRequestMessageForm,
    EquipmentRequestPhotoForm,
    EquipmentRequestForm,
    InventoryAdjustmentForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    RussianAuthenticationForm,
    RussianUserCreationForm,
    UserPreferenceForm,
)
from .quality_report import generate_quality_report, load_quality_report
from .notification_utils import (
    mark_equipment_request_thread_read,
    unread_direct_message_count,
    unread_request_notification_groups,
)

try:
    from anymail.exceptions import AnymailError

    _PASSWORD_RESET_MAIL_ERRORS: tuple[type[BaseException], ...] = (OSError, SMTPException, AnymailError)
except ImportError:
    _PASSWORD_RESET_MAIL_ERRORS = (OSError, SMTPException)

logger = logging.getLogger(__name__)

ROLE_DESCRIPTIONS = {role_name: spec.description for role_name, spec in ROLE_SPECS.items()}

REQUEST_STATUS_HELPERS = {
    REQUEST_PENDING: {
        "badge_class": "badge badge-pending",
        "quick_actions": [
            {"value": REQUEST_APPROVED, "label": "Одобрить"},
            {"value": REQUEST_REJECTED, "label": "Отклонить"},
        ],
    },
    REQUEST_APPROVED: {
        "badge_class": "badge badge-approved",
        "quick_actions": [
            {"value": REQUEST_REJECTED, "label": "Отклонить"},
        ],
    },
    "issued": {"badge_class": "badge badge-issued", "quick_actions": []},
    REQUEST_REJECTED: {
        "badge_class": "badge badge-rejected",
        "quick_actions": [
            {"value": REQUEST_PENDING, "label": "Вернуть на рассмотрение"},
        ],
    },
    "closed": {"badge_class": "badge badge-closed", "quick_actions": []},
}

EQUIPMENT_STATUS_HELPERS = {
    "in_stock": {"badge_class": "badge badge-approved"},
    "assigned": {"badge_class": "badge badge-issued"},
    "checked_out": {"badge_class": "badge badge-pending"},
    "repair": {"badge_class": "badge badge-rejected"},
    "retired": {"badge_class": "badge badge-closed"},
}

VISIBLE_EQUIPMENT_STATUSES = ("in_stock", "repair", "retired")


def _request_status_choices_without_closed():
    return [
        (value, label)
        for value, label in EquipmentRequest._meta.get_field("status").choices
        if value not in {"closed", "issued"}
    ]


def _can_manage_timers(user) -> bool:
    return False


def _can_view_all_operational_data(user) -> bool:
    return user_has_capability(user, "warehouse_operations") or user_has_capability(user, "request_processing")


def _can_access_history(user) -> bool:
    return user_has_capability(user, "users_and_site_admin")


def _can_access_reports(user) -> bool:
    return user_has_capability(user, "report_access")


def _can_access_analytics_dashboard(user) -> bool:
    """Сводка «Аналитика»: сисадмин, те же кто видит отчёты, склад, обработка заявок, админы портала."""
    if not user or not user.is_authenticated:
        return False
    if user_in_group(user, GROUP_ADMIN):
        return True
    return (
        user_has_capability(user, "report_access")
        or user_has_capability(user, "warehouse_operations")
        or user_has_capability(user, "request_processing")
        or user_has_capability(user, "users_and_site_admin")
    )


def _can_access_data_tools(user) -> bool:
    return user_has_capability(user, "data_tools_access")


def _can_import_backup(user) -> bool:
    if not user_has_capability(user, "users_and_site_admin"):
        return False
    if getattr(user, "is_superuser", False):
        return True
    # Только «Sysadmin» может скачивать инструменты, но не импортировать (см. RoleEnforcementWebTests).
    if user.groups.filter(name="Sysadmin").exists():
        return user.groups.filter(name="Administrator").exists()
    return True


def _can_access_quality_report(user) -> bool:
    return user_has_capability(user, "quality_access")


def _can_create_request(user) -> bool:
    return user_has_capability(user, "request_creation")


def _can_process_request_status(user) -> bool:
    return user_has_capability(user, "request_processing")


def _can_delete_request_messages(user) -> bool:
    return user_has_capability(user, "request_processing") or user_has_capability(
        user, "users_and_site_admin"
    )


def _can_delete_specific_request_message(user, msg: EquipmentRequestMessage) -> bool:
    if not _can_delete_request_messages(user):
        return False
    # Главный техник может удалять только свои сообщения.
    if user_in_group(user, GROUP_SENIOR_TECHNICIAN) and not user_has_capability(user, "users_and_site_admin"):
        return msg.author_id == user.pk
    return True


def _can_access_requests_module(user) -> bool:
    """Журнал заявок и экспорт: склад, обработка/создание заявок или админы портала."""
    return (
        user_has_capability(user, "request_creation")
        or user_has_capability(user, "request_processing")
        or user_has_capability(user, "warehouse_operations")
        or user_has_capability(user, "users_and_site_admin")
    )


def _can_create_checkout(user) -> bool:
    return user_has_capability(user, "checkout_operations")


def _can_return_checkout(user, checkout: EquipmentCheckout) -> bool:
    return (
        user_has_capability(user, "warehouse_operations")
        or checkout.taken_by_id == user.pk
    )


def _default_landing_url(user) -> str:
    if not user or not user.is_authenticated:
        return reverse("login")
    if user_has_capability(user, "warehouse_operations"):
        return reverse("equipment_list")
    if user_has_capability(user, "request_creation") or user_has_capability(user, "request_processing"):
        return reverse("request_history")
    if user_has_capability(user, "users_and_site_admin"):
        return reverse("portal_home")
    if _can_access_analytics_dashboard(user):
        return reverse("analytics")
    if _can_access_quality_report(user):
        return reverse("quality_report")
    if _can_access_data_tools(user):
        return reverse("data_tools")
    return reverse("about_site")


def _decorate_request(item: EquipmentRequest):
    helper = REQUEST_STATUS_HELPERS.get(item.status, {})
    item.badge_class = helper.get("badge_class", "badge")
    item.quick_actions = helper.get("quick_actions", [])
    return item


def _decorate_equipment(item: Equipment):
    helper = EQUIPMENT_STATUS_HELPERS.get(item.status, {})
    bc = helper.get("badge_class", "badge")
    item.status_badge_class = bc
    item.badge_class = bc
    return item


def _build_request_message_thread(messages_qs):
    items = list(messages_qs)
    children_map = {}
    for msg in items:
        children_map.setdefault(msg.parent_id, []).append(msg)
    for key in children_map:
        children_map[key].sort(key=lambda x: (x.created_at, x.pk))

    ordered = []

    def walk(parent_id, depth):
        for msg in children_map.get(parent_id, []):
            msg.thread_depth = min(depth, 5)
            ordered.append(msg)
            walk(msg.pk, depth + 1)

    walk(None, 0)
    return ordered


def _message_conversation_summaries(user):
    message_qs = (
        DirectMessage.objects.filter(Q(sender=user) | Q(recipient=user))
        .select_related("sender", "recipient")
        .order_by("-created_at", "-id")
    )
    summaries = {}
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
    return list(summaries.values())


PASSWORD_RESET_CODE_TTL_MINUTES = 15


def _password_reset_delivery_hint() -> str:
    """Текст для UI: почему код может не оказаться в ящике (Mailpit, SMTP, несовпадение email в БД)."""
    from core.registration_domains import registration_domains_display

    domains_part = registration_domains_display()
    parts = [
        f"Код отправляется только если в системе есть активный пользователь с тем же адресом на одном из допустимых доменов ({domains_part}), что вы ввели.",
        "Проверьте папку «Спам».",
    ]
    if getattr(settings, "DEBUG", False):
        host = (getattr(settings, "EMAIL_HOST", "") or "").strip().lower()
        if host == "mailpit":
            parts.append(
                "Сейчас DEBUG и почта уходит в Mailpit, а не на реальный ящик — откройте веб-интерфейс Mailpit "
                "(в docker-compose обычно http://localhost:18025 или порт из MPTTOOLS_MAILPIT_UI_PORT)."
            )
        elif host == "smtp.gmail.com":
            parts.append(
                "Сейчас отправка через Gmail SMTP (GMAIL_SMTP_USER / пароль приложения). Проверьте папку «Спам» и настройки аккаунта Google."
            )
        else:
            parts.append(
                "В DEBUG проверьте EMAIL_HOST / EMAIL_PORT в окружении или задайте "
                "EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend — код появится в консоли сервера."
            )
    else:
        parts.append(
            "На сервере должны быть настроены исходящая почта (EMAIL_* или ANYMAIL_*). При ошибке отправки "
            "на странице появится сообщение об этом, а не перенаправление дальше."
        )
    return " ".join(parts)


def _password_reset_success_hint() -> str:
    """Доп. строка к сообщению об отправке кода — зависит от способа доставки почты."""
    host = (getattr(settings, "EMAIL_HOST", "") or "").strip().lower()
    if host == "mailpit":
        return "Если письма нет — откройте веб-интерфейс Mailpit или проверьте, что адрес в базе совпадает с введённым."
    if host == "smtp.gmail.com":
        return "Если письма нет — проверьте «Спам», папку «Промоакции» и настройки Gmail (пароль приложения, не обычный пароль)."
    return "Если письма нет — чаще всего адрес в базе не совпадает с введённым или есть ошибка настройки SMTP."


def _password_reset_code_hash(email: str, code: str) -> str:
    normalized_email = (email or "").strip().lower()
    return hashlib.sha256(f"{normalized_email}:{code}".encode("utf-8")).hexdigest()


def _generate_password_reset_code() -> str:
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def _send_password_reset_email(*, request, user: User, to_email: str, code: str) -> None:
    """Send plain-text + HTML recovery email. Raises on delivery failure."""
    username = user.get_username()
    confirm_path = reverse("password_reset_confirm")
    confirm_url = request.build_absolute_uri(confirm_path)
    subject = "Код восстановления пароля"
    plain = (
        f"Здравствуйте, {username}!\n\n"
        f"Код для восстановления пароля: {code}\n"
        f"Код действует {PASSWORD_RESET_CODE_TTL_MINUTES} минут.\n\n"
        f"Страница для ввода кода: {confirm_url}\n\n"
        "Если вы не запрашивали восстановление, просто проигнорируйте это письмо."
    )
    safe_username = escape(username)
    safe_url = escape(confirm_url)
    html = (
        "<!DOCTYPE html><html><body style=\"font-family:system-ui,sans-serif;line-height:1.5;color:#1f2a44;\">"
        f"<p>Здравствуйте, {safe_username}!</p>"
        "<p>Код для восстановления пароля:</p>"
        f"<p style=\"font-size:1.5rem;letter-spacing:0.2em;font-weight:600;\">{code}</p>"
        f"<p>Код действует <strong>{PASSWORD_RESET_CODE_TTL_MINUTES}</strong> минут.</p>"
        f"<p><a href=\"{safe_url}\">Открыть страницу ввода кода</a></p>"
        "<p style=\"color:#5c6478;font-size:0.9rem;\">Если вы не запрашивали восстановление, проигнорируйте это письмо.</p>"
        "</body></html>"
    )
    send_multipart_email(subject=subject, plain_body=plain, html_body=html, to=[to_email])


def forbidden(request, message: str):
    back_url = request.META.get("HTTP_REFERER") or _default_landing_url(request.user)
    return render(
        request,
        "inventory/forbidden.html",
        {"message": message, "back_url": back_url},
        status=403,
    )


def _get_user_preferences(user):
    if not user or not user.is_authenticated:
        return None
    try:
        preference = getattr(user, "preferences", None)
        if preference is not None:
            return preference
        preference, _ = UserPreference.objects.get_or_create(user=user)
        return preference
    except (ProgrammingError, OperationalError):
        return None


def _paginate(request, items, page_size: int):
    paginator = Paginator(items, page_size)
    return paginator.get_page(request.GET.get("page") or 1)


def _with_page_context(page_obj):
    return {
        "page_obj": page_obj,
        "page_size": page_obj.paginator.per_page,
        "total_rows": page_obj.paginator.count,
    }


def _export_querystring(params: dict) -> str:
    return urlencode({key: value for key, value in params.items() if value not in (None, "", [])})


def _request_history_filtered_queryset(request):
    preferences = _get_user_preferences(request.user)
    show_deleted = bool(request.session.get("show_deleted_global", False))
    requests_manager = EquipmentRequest.all_objects if show_deleted else EquipmentRequest.objects
    requests_qs = requests_manager.select_related(
        "requester", "equipment", "workplace", "cabinet", "processed_by"
    ).order_by("-requested_at")
    view_mode = request.GET.get("view", "").strip()
    # Обработчики по умолчанию видят очередь (как бывшая отдельная «Обработка заявок»);
    # полный список — чип «Все заявки» с view=all.
    if (
        not view_mode
        and "view" not in request.GET
        and _can_process_request_status(request.user)
    ):
        view_mode = "processing"

    if not _can_view_all_operational_data(request.user):
        requests_qs = requests_qs.filter(requester=request.user)
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    if not status and "status" not in request.GET and preferences and preferences.default_request_status:
        status = preferences.default_request_status
    if not kind and "kind" not in request.GET and preferences and preferences.default_request_kind:
        kind = preferences.default_request_kind
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    if view_mode == "mine":
        requests_qs = requests_qs.filter(requester=request.user)
    elif view_mode == "processing":
        requests_qs = requests_qs.filter(status__in=[REQUEST_PENDING, REQUEST_APPROVED])
    # view=all или пусто (у ролей без обработки): без доп. отбора по режиму просмотра

    if status:
        requests_qs = requests_qs.filter(status=status)
    if kind:
        requests_qs = requests_qs.filter(request_kind=kind)
    if date_from:
        parsed_from = parse_date(date_from)
        if parsed_from is not None:
            from_dt = timezone.make_aware(datetime.combine(parsed_from, time.min), timezone.get_current_timezone())
            requests_qs = requests_qs.filter(requested_at__gte=from_dt)
    if date_to:
        parsed_to = parse_date(date_to)
        if parsed_to is not None:
            to_exclusive = parsed_to + timedelta(days=1)
            to_dt = timezone.make_aware(datetime.combine(to_exclusive, time.min), timezone.get_current_timezone())
            requests_qs = requests_qs.filter(requested_at__lt=to_dt)

    filters = {"status": status, "kind": kind, "from": date_from, "to": date_to, "view": view_mode}
    return requests_qs, filters


def _build_analytics_context(
    *,
    recent_requests_limit: int = 10,
) -> dict:
    start_date = timezone.now().date() - timedelta(days=29)
    equipment_total = Equipment.objects.count()
    consumables_total = Equipment.objects.filter(is_consumable=True).count()
    low_stock_total = Equipment.objects.filter(quantity_available__lte=F("low_stock_threshold")).count()
    threshold_over_total_count = Equipment.objects.filter(low_stock_threshold__gt=F("quantity_total")).count()
    threshold_over_total_items = list(
        Equipment.objects.filter(low_stock_threshold__gt=F("quantity_total"))
        .values("name", "inventory_number", "quantity_total", "low_stock_threshold")
        .order_by("name")[:20]
    )
    requests_pending = EquipmentRequest.objects.filter(status=REQUEST_PENDING).count()
    usage_records_30d = MaterialUsage.objects.filter(used_at__date__gte=start_date).count()

    equipment_status_labels = dict(Equipment._meta.get_field("status").choices)
    equipment_by_status_raw = Equipment.objects.values("status").annotate(count=Count("id")).order_by("status")
    equipment_by_status = [
        {"status": equipment_status_labels.get(item["status"], item["status"]), "count": item["count"]}
        for item in equipment_by_status_raw
    ]

    request_status_labels = dict(EquipmentRequest._meta.get_field("status").choices)
    request_by_status_raw = EquipmentRequest.objects.values("status").annotate(count=Count("id")).order_by("status")
    requests_by_status = [
        {"status": request_status_labels.get(item["status"], item["status"]), "count": item["count"]}
        for item in request_by_status_raw
    ]

    recent_requests = list(
        EquipmentRequest.objects.select_related("requester", "equipment", "workplace", "cabinet")
        .order_by("-requested_at")[:recent_requests_limit]
    )
    days = [start_date + timedelta(days=idx) for idx in range(30)]
    day_labels = [day.isoformat() for day in days]

    def series_from_queryset(queryset, field_name):
        counts = {day: 0 for day in days}
        for row in queryset:
            day = row["day"]
            if day in counts:
                counts[day] = row[field_name]
        return [counts[day] for day in days]

    request_daily_qs = (
        EquipmentRequest.objects.filter(requested_at__date__gte=start_date)
        .annotate(day=TruncDate("requested_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    usage_consumable_daily_qs = (
        MaterialUsage.objects.filter(used_at__date__gte=start_date, equipment__is_consumable=True)
        .annotate(day=TruncDate("used_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    usage_non_consumable_daily_qs = (
        MaterialUsage.objects.filter(used_at__date__gte=start_date)
        .filter(Q(equipment__is_consumable=False) | Q(equipment__isnull=True))
        .annotate(day=TruncDate("used_at"))
        .values("day")
        .annotate(count=Count("id"))
    )

    category_stock_raw = (
        Equipment.objects.values("category__name")
        .annotate(total=Sum("quantity_total"))
        .order_by("category__name")
    )
    category_stock = [
        {
            "category": item["category__name"] or "Uncategorized",
            "total": item["total"] or 0,
        }
        for item in category_stock_raw
    ]

    consumable_qty_30d = (
        MaterialUsage.objects.filter(used_at__date__gte=start_date, equipment__is_consumable=True).aggregate(
            t=Sum("quantity")
        )["t"]
        or 0
    )
    non_consumable_qty_30d = (
        MaterialUsage.objects.filter(used_at__date__gte=start_date)
        .filter(Q(equipment__is_consumable=False) | Q(equipment__isnull=True))
        .aggregate(t=Sum("quantity"))["t"]
        or 0
    )

    top_consumable_usage_raw = list(
        MaterialUsage.objects.filter(
            used_at__date__gte=start_date,
            equipment__is_consumable=True,
            equipment_id__isnull=False,
        )
        .values("equipment__name")
        .annotate(qty=Sum("quantity"))
        .order_by("-qty")[:10]
    )
    top_consumable_usage = [
        {"name": row["equipment__name"] or "—", "qty": int(row["qty"] or 0)} for row in top_consumable_usage_raw
    ]

    return {
        "equipment_total": equipment_total,
        "consumables_total": consumables_total,
        "low_stock_total": low_stock_total,
        "threshold_over_total_count": threshold_over_total_count,
        "threshold_over_total_items": threshold_over_total_items,
        "requests_pending": requests_pending,
        "usage_records_30d": usage_records_30d,
        "recent_requests": recent_requests,
        "equipment_by_status": equipment_by_status,
        "requests_by_status": requests_by_status,
        "day_labels": day_labels,
        "requests_daily": series_from_queryset(request_daily_qs, "count"),
        "usage_consumable_daily": series_from_queryset(usage_consumable_daily_qs, "count"),
        "usage_non_consumable_daily": series_from_queryset(usage_non_consumable_daily_qs, "count"),
        "category_stock": category_stock,
        "usage_qty_totals_30d": {"consumable": consumable_qty_30d, "non_consumable": non_consumable_qty_30d},
        "top_consumable_usage": top_consumable_usage,
    }


def _zip_csv_bytes(filename: str, header: list[str], rows: list[list]) -> tuple[str, bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return filename, buf.getvalue().encode("utf-8-sig")


def _pdf_table_response(*, title: str, headers: list[str], rows: list[list], filename: str) -> HttpResponse:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=20,
    )
    font_name = "Helvetica"
    try:
        windows_font = Path("C:/Windows/Fonts/arial.ttf")
        if windows_font.exists():
            pdfmetrics.registerFont(TTFont("ArialUnicode", str(windows_font)))
            font_name = "ArialUnicode"
    except Exception:
        font_name = "Helvetica"

    styles = getSampleStyleSheet()
    title_style = styles["Heading3"].clone("pdf_title")
    title_style.fontName = font_name
    body_style = styles["BodyText"].clone("pdf_body")
    body_style.fontName = font_name

    safe_rows = [[str(cell) if cell is not None else "" for cell in row] for row in rows]
    table_data = [headers, *safe_rows]
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2ff")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f2a44")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    doc.build([Paragraph(title, title_style), Spacer(1, 8), table, Spacer(1, 8), Paragraph("Сформировано системой MPT Tools.", body_style)])
    pdf_bytes = buffer.getvalue()
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _build_vertical_bar_chart(
    *,
    title: str,
    categories: list[str],
    data_series: list[list[float]],
    series_colors: list,
    width: int,
    height: int,
    font_name: str,
) -> Drawing:
    drawing = Drawing(width, height)
    drawing.add(String(8, height - 14, title, fontName=font_name, fontSize=10, fillColor=colors.HexColor("#1f2a44")))

    chart = VerticalBarChart()
    chart.x = 36
    chart.y = 32
    chart.width = width - 52
    chart.height = height - 56
    chart.data = data_series or [[0]]
    chart.categoryAxis.categoryNames = categories or ["—"]
    chart.categoryAxis.labels.boxAnchor = "ne"
    chart.categoryAxis.labels.angle = 25
    chart.categoryAxis.labels.fontName = font_name
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontName = font_name
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    max_value = 0
    for row in chart.data:
        if row:
            max_value = max(max_value, max(row))
    chart.valueAxis.valueMax = max(1, int(math.ceil(max_value * 1.2)))
    chart.valueAxis.valueStep = max(1, int(math.ceil(chart.valueAxis.valueMax / 5)))
    for idx, row in enumerate(chart.data):
        if idx < len(series_colors):
            chart.bars[idx].fillColor = series_colors[idx]
    drawing.add(chart)
    return drawing


def _analytics_pdf_with_charts(ctx: dict) -> HttpResponse:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=20,
    )
    font_name = "Helvetica"
    try:
        windows_font = Path("C:/Windows/Fonts/arial.ttf")
        if windows_font.exists():
            pdfmetrics.registerFont(TTFont("ArialUnicode", str(windows_font)))
            font_name = "ArialUnicode"
    except Exception:
        font_name = "Helvetica"

    styles = getSampleStyleSheet()
    title_style = styles["Heading3"].clone("analytics_pdf_title")
    title_style.fontName = font_name
    body_style = styles["BodyText"].clone("analytics_pdf_body")
    body_style.fontName = font_name

    kpi_rows = [
        ["Всего оборудования", ctx["equipment_total"]],
        ["Расходники", ctx["consumables_total"]],
        ["Низкий остаток", ctx["low_stock_total"]],
        ["Ожидают обработки", ctx["requests_pending"]],
        ["Операции расхода за 30 дней", ctx["usage_records_30d"]],
    ]
    kpi_table = Table([["Показатель", "Значение"], *kpi_rows], repeatRows=1, colWidths=[340, 120])
    kpi_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2ff")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    equipment_categories = [str(row["status"]) for row in ctx.get("equipment_by_status", [])]
    equipment_values = [int(row["count"]) for row in ctx.get("equipment_by_status", [])]
    request_categories = [str(row["status"]) for row in ctx.get("requests_by_status", [])]
    request_values = [int(row["count"]) for row in ctx.get("requests_by_status", [])]
    day_labels = [str(v)[5:] for v in ctx.get("day_labels", [])[-10:]]
    req_daily = [int(v) for v in ctx.get("requests_daily", [])[-10:]]
    usage_c_daily = [int(v) for v in ctx.get("usage_consumable_daily", [])[-10:]]
    usage_nc_daily = [int(v) for v in ctx.get("usage_non_consumable_daily", [])[-10:]]

    chart_equipment = _build_vertical_bar_chart(
        title="Оборудование по статусам",
        categories=equipment_categories,
        data_series=[equipment_values],
        series_colors=[colors.HexColor("#748cab")],
        width=360,
        height=190,
        font_name=font_name,
    )
    chart_requests = _build_vertical_bar_chart(
        title="Заявки по статусам",
        categories=request_categories,
        data_series=[request_values],
        series_colors=[colors.HexColor("#d4572a")],
        width=360,
        height=190,
        font_name=font_name,
    )
    chart_activity = _build_vertical_bar_chart(
        title="Активность за 10 дней (заявки / расходники / нерасходуемое)",
        categories=day_labels,
        data_series=[req_daily, usage_c_daily, usage_nc_daily],
        series_colors=[colors.HexColor("#d4572a"), colors.HexColor("#0f5e4b"), colors.HexColor("#748cab")],
        width=760,
        height=230,
        font_name=font_name,
    )

    chart_row = Table([[chart_equipment, chart_requests]], colWidths=[370, 370])
    chart_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    elements = [
        Paragraph("Аналитика: сводка и графики", title_style),
        Spacer(1, 8),
        kpi_table,
        Spacer(1, 10),
        chart_row,
        Spacer(1, 10),
        chart_activity,
        Spacer(1, 8),
        Paragraph("Сформировано системой MPT Tools.", body_style),
    ]
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="analytics-summary-charts.pdf"'
    return response


@login_required
def analytics(request):
    if not _can_access_analytics_dashboard(request.user):
        return forbidden(request, "Раздел «Аналитика» недоступен для вашей роли.")
    return render(request, "inventory/analytics.html", _build_analytics_context())


@login_required
def analytics_export_csv_zip(request):
    if not _can_access_analytics_dashboard(request.user):
        return forbidden(request, "Экспорт недоступен для вашей роли.")
    ctx = _build_analytics_context(recent_requests_limit=8000)
    files: list[tuple[str, bytes]] = []
    files.append(
        _zip_csv_bytes(
            "01_kpi.csv",
            ["metric", "value"],
            [
                ["equipment_total", ctx["equipment_total"]],
                ["consumables_total", ctx["consumables_total"]],
                ["low_stock_total", ctx["low_stock_total"]],
                ["requests_pending", ctx["requests_pending"]],
                ["usage_records_30d", ctx["usage_records_30d"]],
            ],
        )
    )
    files.append(
        _zip_csv_bytes(
            "02_equipment_by_status.csv",
            ["status", "count"],
            [[row["status"], row["count"]] for row in ctx["equipment_by_status"]],
        )
    )
    files.append(
        _zip_csv_bytes(
            "03_requests_by_status.csv",
            ["status", "count"],
            [[row["status"], row["count"]] for row in ctx["requests_by_status"]],
        )
    )
    files.append(
        _zip_csv_bytes(
            "04_category_stock.csv",
            ["category", "total_on_books"],
            [[row["category"], row["total"]] for row in ctx["category_stock"]],
        )
    )
    activity_rows = []
    for idx, day in enumerate(ctx["day_labels"]):
        activity_rows.append(
            [
                day,
                ctx["requests_daily"][idx],
                ctx["usage_consumable_daily"][idx],
                ctx["usage_non_consumable_daily"][idx],
            ]
        )
    files.append(
        _zip_csv_bytes(
            "05_activity_daily_30d.csv",
            ["date", "requests", "usage_consumable_ops", "usage_non_consumable_ops"],
            activity_rows,
        )
    )
    req_rows = []
    for r in ctx["recent_requests"]:
        req_rows.append(
            [
                r.pk,
                r.requester.get_username() if r.requester_id else "",
                str(r.equipment) if r.equipment_id else "",
                r.quantity,
                r.get_status_display(),
                timezone.localtime(r.requested_at).isoformat() if r.requested_at else "",
                str(r.workplace) if r.workplace_id else "",
                str(r.cabinet) if r.cabinet_id else "",
            ]
        )
    files.append(
        _zip_csv_bytes(
            "06_recent_requests.csv",
            ["id", "requester", "equipment", "quantity", "status", "requested_at", "workplace", "cabinet"],
            req_rows,
        )
    )
    qty_totals = ctx["usage_qty_totals_30d"]
    files.append(
        _zip_csv_bytes(
            "07_usage_qty_totals_30d.csv",
            ["kind", "quantity"],
            [
                ["consumable", qty_totals["consumable"]],
                ["non_consumable", qty_totals["non_consumable"]],
            ],
        )
    )
    top_rows = [[row["name"], row["qty"]] for row in ctx["top_consumable_usage"]]
    files.append(
        _zip_csv_bytes(
            "08_top_consumables_30d.csv",
            ["equipment_name", "quantity"],
            top_rows,
        )
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="analytics-tables.zip"'
    return response


@login_required
def analytics_print(request):
    if not _can_access_analytics_dashboard(request.user):
        return forbidden(request, "Раздел «Аналитика» недоступен для вашей роли.")
    ctx = _build_analytics_context(recent_requests_limit=250)
    activity_rows = []
    for idx, day in enumerate(ctx["day_labels"]):
        activity_rows.append(
            {
                "date": day,
                "requests": ctx["requests_daily"][idx],
                "usage_consumable": ctx["usage_consumable_daily"][idx],
                "usage_non_consumable": ctx["usage_non_consumable_daily"][idx],
            }
        )
    ctx["activity_table_rows"] = activity_rows
    if request.GET.get("download") == "1":
        return _analytics_pdf_with_charts(ctx)
    return render(request, "inventory/analytics_print.html", ctx)


def _equipment_list_filtered_queryset(request):
    show_deleted = bool(request.session.get("show_deleted_global", False))
    manager = Equipment.all_objects if show_deleted else Equipment.objects
    queryset = manager.select_related("category", "workplace")

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    category = request.GET.get("category", "").strip()
    workplace = request.GET.get("workplace", "").strip()
    consumable = request.GET.get("consumable", "").strip()
    low_stock = request.GET.get("low_stock", "").strip()

    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(model__icontains=query)
        )

    status_base_qs = queryset
    if status and status not in VISIBLE_EQUIPMENT_STATUSES:
        status = ""

    if status:
        queryset = queryset.filter(status=status)

    if category:
        queryset = queryset.filter(category_id=category)

    if workplace:
        queryset = queryset.filter(workplace_id=workplace)

    if consumable:
        queryset = queryset.filter(is_consumable=consumable == "1")

    if low_stock:
        queryset = queryset.filter(quantity_available__lte=F("low_stock_threshold"))

    list_filters = {
        "q": query,
        "status": status,
        "category": category,
        "workplace": workplace,
        "consumable": consumable,
        "low_stock": low_stock,
        "show_deleted": "1" if show_deleted else "",
    }
    return queryset.order_by("name", "inventory_number"), list_filters, status_base_qs


@login_required
def equipment_export_csv(request):
    queryset, list_filters, _sb = _equipment_list_filtered_queryset(request)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "name",
            "inventory_number",
            "serial_number",
            "model",
            "category",
            "workplace",
            "status",
            "is_consumable",
            "quantity_total",
            "quantity_available",
            "low_stock_threshold",
            "deleted_at",
        ]
    )
    for eq in queryset.iterator(chunk_size=500):
        writer.writerow(
            [
                eq.pk,
                eq.name,
                eq.inventory_number,
                eq.serial_number,
                eq.model,
                eq.category.name if eq.category_id else "",
                eq.workplace.name if eq.workplace_id else "",
                eq.get_status_display(),
                "1" if eq.is_consumable else "0",
                eq.quantity_total,
                eq.quantity_available,
                eq.low_stock_threshold,
                eq.deleted_at.isoformat() if eq.deleted_at else "",
            ]
        )
    response = HttpResponse(buffer.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="equipment-export.csv"'
    return response


@login_required
def equipment_print(request):
    queryset, list_filters, _sb = _equipment_list_filtered_queryset(request)
    total = queryset.count()
    cap = 8000
    items = list(queryset[:cap])
    if request.GET.get("download") == "1":
        rows = [
            [
                item.pk,
                item.name,
                item.serial_number or item.inventory_number or "-",
                item.get_status_display(),
                "Да" if item.is_consumable else "Нет",
                item.quantity_total,
                item.quantity_available,
            ]
            for item in items
        ]
        return _pdf_table_response(
            title="Склад: список оборудования",
            headers=["ID", "Название", "Серийный номер", "Статус", "Расходник", "Количество", "Доступно"],
            rows=rows,
            filename="equipment-list.pdf",
        )
    return render(
        request,
        "inventory/equipment_print.html",
        {
            "equipment": items,
            "filters": list_filters,
            "export_query": _export_querystring(list_filters),
            "exported_count": len(items),
            "total_matching": total,
            "truncated": total > len(items),
        },
    )


@login_required
def equipment_list(request):
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    show_deleted = bool(request.session.get("show_deleted_global", False))
    can_manage_equipment = (
        user_has_capability(request.user, "warehouse_operations")
    )

    if request.method == "POST":
        if not can_manage_equipment:
            return forbidden(request, "Управление оборудованием доступно только уполномоченным ролям.")
        action = (request.POST.get("action") or "").strip()
        next_url = (request.POST.get("next") or "").strip() or reverse("equipment_list")
        if action == "status":
            equipment_id = (request.POST.get("equipment_id") or "").strip()
            new_status = (request.POST.get("status") or "").strip()
            if not equipment_id.isdigit():
                messages.error(request, "Не удалось определить запись оборудования.")
                return redirect(next_url)
            allowed_statuses = {value for value, _ in Equipment._meta.get_field("status").choices}
            if new_status not in allowed_statuses:
                messages.error(request, "Некорректный статус оборудования.")
                return redirect(next_url)
            item = get_object_or_404(Equipment.all_objects.select_related("category", "workplace"), pk=int(equipment_id))
            item.status = new_status
            item._actor = request.user
            item.save(update_fields=["status"])
            messages.success(request, "Статус оборудования обновлён.")
            return redirect(next_url)
        elif action == "toggle_delete":
            equipment_id = (request.POST.get("equipment_id") or "").strip()
            if not equipment_id.isdigit():
                messages.error(request, "Не удалось определить запись оборудования.")
                return redirect(next_url)
            item = get_object_or_404(Equipment.all_objects.select_related("category", "workplace"), pk=int(equipment_id))
            if item.deleted_at:
                item.restore()
                messages.success(request, "Оборудование восстановлено.")
            else:
                item.delete()
                messages.success(request, "Оборудование перемещено в удалённые.")
            return redirect(next_url)

    queryset, list_filters, status_base_qs = _equipment_list_filtered_queryset(request)
    query = list_filters["q"]
    status = list_filters["status"]
    category = list_filters["category"]
    workplace = list_filters["workplace"]
    consumable = list_filters["consumable"]

    page_obj = _paginate(request, queryset, page_size)
    equipment_items = [_decorate_equipment(item) for item in page_obj.object_list]
    status_counts_raw = status_base_qs.values("status").annotate(count=Count("id"))
    status_counts = {item["status"]: item["count"] for item in status_counts_raw}

    base_filters = {
        "q": query,
        "category": category,
        "workplace": workplace,
        "consumable": consumable,
    }
    status_filter_links = []
    all_query = urlencode({key: value for key, value in base_filters.items() if value})
    status_filter_links.append(
        {
            "label": "Все статусы",
            "value": "",
            "count": status_base_qs.count(),
            "is_active": not status,
            "url": f"{reverse('equipment_list')}?{all_query}" if all_query else reverse("equipment_list"),
        }
    )
    status_label_map = dict(Equipment._meta.get_field("status").choices)
    for value in VISIBLE_EQUIPMENT_STATUSES:
        label = status_label_map.get(value, value)
        query_with_status = {**base_filters, "status": value}
        query_with_status = {key: item for key, item in query_with_status.items() if item}
        encoded = urlencode(query_with_status)
        status_filter_links.append(
            {
                "label": label,
                "value": value,
                "count": status_counts.get(value, 0),
                "is_active": status == value,
                "url": f"{reverse('equipment_list')}?{encoded}" if encoded else reverse("equipment_list"),
            }
        )

    context = {
        "equipment": equipment_items,
        "categories": EquipmentCategory.objects.all(),
        "workplaces": Workplace.objects.all(),
        "status_choices": [(value, status_label_map.get(value, value)) for value in VISIBLE_EQUIPMENT_STATUSES],
        "status_filter_links": status_filter_links,
        "can_manage_equipment": can_manage_equipment,
        "filters": list_filters,
        "export_query": _export_querystring(list_filters),
        **_with_page_context(page_obj),
    }
    return render(request, "inventory/equipment_list.html", context)


@login_required
def warehouse_restock(request):
    messages.info(request, "Форма пополнения перенесена в обычные заявки: выберите тип «Пополнение».")
    return redirect("request_create")


@login_required
def equipment_detail(request, equipment_id: int):
    show_deleted = bool(request.session.get("show_deleted_global", False))
    manager = Equipment.all_objects if show_deleted else Equipment.objects
    item = get_object_or_404(manager.select_related("category", "workplace"), pk=equipment_id)
    can_manage_equipment = (
        user_has_capability(request.user, "warehouse_operations")
    )
    split_repair_max_qty = 0
    if can_manage_equipment and not item.is_consumable and item.quantity_total > 1:
        split_repair_max_qty = min(item.quantity_available, item.quantity_total - 1)
    return render(
        request,
        "inventory/equipment_detail.html",
        {
            "item": _decorate_equipment(item),
            "can_manage_equipment": can_manage_equipment,
            "split_repair_max_qty": split_repair_max_qty,
        },
    )


def equipment_public_card(request, equipment_id: int):
    item = get_object_or_404(Equipment.objects.select_related("category", "workplace"), pk=equipment_id)
    return render(
        request,
        "inventory/equipment_detail.html",
        {
            "item": _decorate_equipment(item),
            "can_manage_equipment": False,
            "split_repair_max_qty": 0,
            "is_public_card": True,
            "show_qr_link": False,
        },
    )


@login_required
@require_POST
def equipment_split_repair(request, equipment_id: int):
    """Выделить часть количества нерасходуемой позиции в отдельную карточку со статусом «В ремонте»."""
    show_deleted = bool(request.session.get("show_deleted_global", False))
    manager = Equipment.all_objects if show_deleted else Equipment.objects
    item = get_object_or_404(manager.select_related("category", "workplace"), pk=equipment_id)
    can_manage = (
        user_has_capability(request.user, "warehouse_operations")
    )
    if not can_manage:
        return forbidden(request, "Недостаточно прав для изменения складской позиции.")
    if item.is_consumable:
        messages.error(request, "Для расходников количество уменьшается при одобрении заявки.")
        return redirect("equipment_detail", equipment_id=item.pk)
    if item.quantity_total <= 1:
        messages.error(
            request,
            "При одной единице откройте «Редактировать» и смените статус на «В ремонте».",
        )
        return redirect("equipment_detail", equipment_id=item.pk)
    raw_qty = (request.POST.get("qty") or "1").strip()
    unit_serial = (request.POST.get("unit_serial") or "").strip()
    if not unit_serial:
        messages.error(
            request,
            "Укажите уникальный номер единицы (серийный или внутренний), которая уходит в ремонт.",
        )
        return redirect("equipment_detail", equipment_id=item.pk)
    if not raw_qty.isdigit():
        messages.error(request, "Некорректное количество.")
        return redirect("equipment_detail", equipment_id=item.pk)
    qty = int(raw_qty)
    if qty < 1:
        messages.error(request, "Количество должно быть не меньше 1.")
        return redirect("equipment_detail", equipment_id=item.pk)
    if qty >= item.quantity_total:
        messages.error(
            request,
            "Чтобы отправить все единицы в ремонт, откройте «Редактировать» и установите статус «В ремонте» для этой позиции.",
        )
        return redirect("equipment_detail", equipment_id=item.pk)
    if qty > item.quantity_available:
        messages.error(request, "Нельзя отправить в ремонт больше, чем сейчас доступно на складе.")
        return redirect("equipment_detail", equipment_id=item.pk)
    if Equipment.all_objects.filter(inventory_number=unit_serial).exists():
        messages.error(request, "Позиция с таким номером уже есть в системе.")
        return redirect("equipment_detail", equipment_id=item.pk)

    repair_row = None
    try:
        with transaction.atomic():
            updated = Equipment.objects.filter(
                pk=item.pk,
                quantity_available__gte=qty,
                quantity_total__gt=qty,
            ).update(
                quantity_total=F("quantity_total") - qty,
                quantity_available=F("quantity_available") - qty,
            )
            if updated != 1:
                messages.error(
                    request,
                    "Не удалось обновить остатки — возможно, позицию уже изменили. Обновите страницу.",
                )
                return redirect("equipment_detail", equipment_id=item.pk)
            repair_row = Equipment(
                name=item.name,
                inventory_number=unit_serial,
                serial_number=unit_serial,
                category=item.category,
                workplace=item.workplace,
                model=item.model,
                is_consumable=False,
                status=STATUS_REPAIR,
                quantity_total=qty,
                quantity_available=qty,
                low_stock_threshold=0,
                purchase_date=item.purchase_date,
                warranty_end=item.warranty_end,
                notes=(
                    f"Выделено в ремонт из позиции #{item.pk} ({item.inventory_number}), {qty} шт."
                ),
            )
            repair_row._actor = request.user
            repair_row.save()
    except Exception:
        logger.exception("equipment_split_repair failed for equipment_id=%s", equipment_id)
        messages.error(request, "Не удалось сохранить изменения.")
        return redirect("equipment_detail", equipment_id=item.pk)

    messages.success(
        request,
        f"Создана позиция «В ремонте» #{repair_row.pk} ({qty} шт.). У исходной позиции #{item.pk} уменьшено количество.",
    )
    return redirect("equipment_detail", equipment_id=repair_row.pk)


@login_required
def usage_history(request):
    return forbidden(request, "Раздел отключён. Расход расходников учитывается при одобрении заявки.")


@login_required
def usage_export_csv(request):
    return forbidden(request, "Раздел отключён. Расход расходников учитывается при одобрении заявки.")


@login_required
def usage_print(request):
    return forbidden(request, "Раздел отключён. Расход расходников учитывается при одобрении заявки.")


@login_required
def request_history(request):
    if not _can_access_requests_module(request.user):
        return forbidden(
            request,
            "Раздел заявок доступен ролям «Техник», «Поддержка первой линии», «Старший техник», «Системный администратор» или «Администратор».",
        )
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    requests_qs, list_filters = _request_history_filtered_queryset(request)

    page_obj = _paginate(request, requests_qs, page_size)
    request_items = [_decorate_request(item) for item in page_obj.object_list]
    can_quick_status = _can_process_request_status(request.user)

    return render(
        request,
        "inventory/request_history.html",
        {
            "requests": request_items,
            "status_choices": _request_status_choices_without_closed(),
            "kind_choices": EquipmentRequest._meta.get_field("request_kind").choices,
            "filters": list_filters,
            "export_query": _export_querystring(list_filters),
            "can_create_request": _can_create_request(request.user),
            "can_quick_status": can_quick_status,
            "can_manage_requests": can_quick_status or user_has_capability(request.user, "users_and_site_admin"),
            "non_consumable_target_status_choices": EquipmentRequest._meta.get_field("non_consumable_target_status").choices,
            **_with_page_context(page_obj),
        },
    )


@login_required
def request_export_csv(request):
    if not _can_access_requests_module(request.user):
        return forbidden(
            request,
            "Экспорт заявок доступен только уполномоченным ролям.",
        )
    requests_qs, _filters = _request_history_filtered_queryset(request)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "requester",
            "request_kind",
            "equipment",
            "workplace",
            "cabinet",
            "quantity",
            "status",
            "comment",
            "requested_at",
            "needed_by",
            "processed_by",
            "processed_at",
            "deleted_at",
        ]
    )
    for row in requests_qs.iterator(chunk_size=500):
        writer.writerow(
            [
                row.pk,
                row.requester.get_username() if row.requester_id else "",
                row.get_request_kind_display(),
                str(row.equipment) if row.equipment_id else "",
                row.workplace.name if row.workplace_id else "",
                row.cabinet.name if row.cabinet_id else "",
                row.quantity,
                row.get_status_display(),
                (row.comment or "").replace("\r\n", "\n"),
                timezone.localtime(row.requested_at).isoformat() if row.requested_at else "",
                row.needed_by.isoformat() if row.needed_by else "",
                row.processed_by.get_username() if row.processed_by_id else "",
                timezone.localtime(row.processed_at).isoformat() if row.processed_at else "",
                row.deleted_at.isoformat() if row.deleted_at else "",
            ]
        )
    response = HttpResponse(buffer.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="requests-export.csv"'
    return response


@login_required
def request_print(request):
    if not _can_access_requests_module(request.user):
        return forbidden(
            request,
            "Печать списка заявок доступна только уполномоченным ролям.",
        )
    requests_qs, list_filters = _request_history_filtered_queryset(request)
    total = requests_qs.count()
    cap = 8000
    rows = list(requests_qs[:cap])
    if request.GET.get("download") == "1":
        pdf_rows = [
            [
                item.pk,
                item.requester.get_username() if item.requester_id else "-",
                str(item.equipment) if item.equipment_id else "—",
                item.quantity,
                item.get_status_display(),
                timezone.localtime(item.requested_at).strftime("%d.%m.%Y %H:%M") if item.requested_at else "-",
            ]
            for item in rows
        ]
        return _pdf_table_response(
            title="Заявки: список",
            headers=["ID", "Заявитель", "Оборудование", "Кол-во", "Статус", "Создана"],
            rows=pdf_rows,
            filename="requests-list.pdf",
        )
    return render(
        request,
        "inventory/request_history_print.html",
        {
            "requests": rows,
            "filters": list_filters,
            "export_query": _export_querystring(list_filters),
            "exported_count": len(rows),
            "total_matching": total,
            "truncated": total > len(rows),
            "non_consumable_target_status_choices": EquipmentRequest._meta.get_field("non_consumable_target_status").choices,
        },
    )


@login_required
def timer_panel(request):
    return forbidden(request, "Раздел таймеров отключён.")


@login_required
def inventory_search(request):
    show_deleted = bool(request.session.get("show_deleted_global", False))
    q = request.GET.get("q", "").strip()
    context = {"q": q, "has_query": bool(q)}
    if not q:
        context.update(
            {
                "equipment_results": [],
                "workplace_results": [],
                "cabinet_results": [],
            }
        )
        return render(request, "inventory/search.html", context)

    equipment_manager = Equipment.all_objects if show_deleted else Equipment.objects
    workplaces_manager = Workplace.all_objects if show_deleted else Workplace.objects
    cabinets_manager = Cabinet.all_objects if show_deleted else Cabinet.objects

    equipment_results = (
        equipment_manager.select_related("category", "workplace")
        .filter(
            Q(name__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(model__icontains=q)
            | Q(category__name__icontains=q)
            | Q(workplace__name__icontains=q)
        )
        .order_by("name")[:25]
    )
    workplace_results = workplaces_manager.filter(
        Q(name__icontains=q) | Q(location__icontains=q) | Q(description__icontains=q)
    ).order_by("name")[:25]
    cabinet_results = cabinets_manager.select_related("workplace").filter(
        Q(name__icontains=q) | Q(workplace__name__icontains=q) | Q(description__icontains=q)
    ).order_by("name")[:25]
    context.update(
        {
            "equipment_results": equipment_results,
            "workplace_results": workplace_results,
            "cabinet_results": cabinet_results,
        }
    )
    return render(request, "inventory/search.html", context)


@login_required
def workplaces(request):
    show_deleted = bool(request.session.get("show_deleted_global", False))
    workplaces_manager = Workplace.all_objects if show_deleted else Workplace.objects
    workplaces_qs = workplaces_manager.all().order_by("name")
    members_manager = WorkplaceMember.all_objects if show_deleted else WorkplaceMember.objects
    members = members_manager.select_related("user", "workplace")
    members_by_workplace = {}
    for member in members:
        members_by_workplace.setdefault(member.workplace_id, []).append(member)

    return render(
        request,
        "inventory/workplaces.html",
        {
            "workplaces": workplaces_qs,
            "members_by_workplace": members_by_workplace,
            "show_deleted": show_deleted,
            "can_manage_workplaces": (
                user_has_capability(request.user, "warehouse_operations")
            ),
        },
    )


@login_required
def suppliers(request):
    return forbidden(request, "Раздел поставщиков отключён.")


@login_required
def cabinets(request):
    show_deleted = bool(request.session.get("show_deleted_global", False))
    cabinets_manager = Cabinet.all_objects if show_deleted else Cabinet.objects
    cabinets_qs = cabinets_manager.select_related("workplace").order_by("name")
    return render(
        request,
        "inventory/cabinets.html",
        {
            "cabinets": cabinets_qs,
            "show_deleted": show_deleted,
            "can_manage_cabinets": (
                user_has_capability(request.user, "warehouse_operations")
            ),
        },
    )


@login_required
def checkouts(request):
    return forbidden(request, "Раздел выдач отключён. Расход расходников — через заявки и их одобрение.")


@login_required
def history_timeline(request):
    if not _can_access_history(request.user):
        return forbidden(request, "История изменений доступна только администратору.")
    logs = AuditLog.objects.select_related("actor", "content_type").order_by("created_at")
    action = request.GET.get("action", "").strip()
    model = request.GET.get("model", "").strip()
    order = request.GET.get("order", "asc").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    if action:
        logs = logs.filter(action=action)
    if model:
        logs = logs.filter(content_type__model=model)
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)
    if order == "desc":
        logs = logs.order_by("-created_at")

    content_types = AuditLog.objects.values_list("content_type__model", flat=True).distinct().order_by("content_type__model")

    return render(
        request,
        "inventory/history.html",
        {
            "logs": logs,
            "filters": {"action": action, "model": model, "order": order, "from": date_from, "to": date_to},
            "content_types": content_types,
        },
    )


def _reports_page_context(request) -> dict:
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    workplace_equipment_report = (
        Equipment.objects.filter(workplace__isnull=False)
        .values("workplace__name")
        .annotate(items=Count("id"), total=Sum("quantity_total"), available=Sum("quantity_available"))
        .order_by("workplace__name")
    )

    usage_qs = MaterialUsage.objects.filter(equipment__is_consumable=True)
    if date_from:
        usage_qs = usage_qs.filter(used_at__date__gte=date_from)
    if date_to:
        usage_qs = usage_qs.filter(used_at__date__lte=date_to)

    usage_summary = usage_qs.values("equipment_id").annotate(used=Sum("quantity"))
    usage_by_equipment = {item["equipment_id"]: item["used"] for item in usage_summary}

    materials = (
        Equipment.objects.filter(is_consumable=True)
        .values("id", "name", "inventory_number")
        .annotate(total=Sum("quantity_total"), available=Sum("quantity_available"))
        .order_by("name")
    )

    materials_report = [
        {
            "name": item["name"],
            "inventory_number": item["inventory_number"],
            "total": item["total"] or 0,
            "available": item["available"] or 0,
            "used": usage_by_equipment.get(item["id"], 0),
        }
        for item in materials
    ]

    return {
        "workplace_equipment_report": workplace_equipment_report,
        "materials_report": materials_report,
        "filters": {"from": date_from, "to": date_to},
    }


@login_required
def reports(request):
    if not _can_access_reports(request.user):
        return forbidden(request, "Отчёты доступны только администратору и складу.")
    return render(request, "inventory/reports.html", _reports_page_context(request))


@login_required
def reports_print(request):
    if not _can_access_reports(request.user):
        return forbidden(request, "Отчёты доступны только администратору и складу.")
    ctx = _reports_page_context(request)
    if request.GET.get("download") == "1":
        rows = [
            [row["name"], row["inventory_number"], row["total"], row["available"], row["used"]]
            for row in ctx["materials_report"]
        ]
        return _pdf_table_response(
            title="Отчёт по материалам",
            headers=["Материал", "Серийный номер", "Всего", "Доступно", "Использовано"],
            rows=rows,
            filename="materials-report.pdf",
        )
    return render(request, "inventory/reports_print.html", ctx)


@login_required
def reports_export(request, report_type: str):
    if not _can_access_reports(request.user):
        return forbidden(request, "Экспорт отчётов доступен только администратору и складу.")
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{report_type}-report.csv"'
    writer = csv.writer(response)

    if report_type in ("cabinets", "workplaces"):
        writer.writerow(["Workplace", "Items", "Total qty", "Available qty"])
        workplace_report = (
            Equipment.objects.filter(workplace__isnull=False)
            .values("workplace__name")
            .annotate(items=Count("id"), total=Sum("quantity_total"), available=Sum("quantity_available"))
            .order_by("workplace__name")
        )
        for item in workplace_report:
            writer.writerow(
                [
                    item["workplace__name"] or "-",
                    item["items"],
                    item["total"] or 0,
                    item["available"] or 0,
                ]
            )
        return response

    if report_type == "materials":
        writer.writerow(["Material", "Inventory", "Total", "Available", "Used"])
        usage_qs = MaterialUsage.objects.filter(equipment__is_consumable=True)
        if date_from:
            usage_qs = usage_qs.filter(used_at__date__gte=date_from)
        if date_to:
            usage_qs = usage_qs.filter(used_at__date__lte=date_to)
        usage_summary = usage_qs.values("equipment_id").annotate(used=Sum("quantity"))
        usage_by_equipment = {item["equipment_id"]: item["used"] for item in usage_summary}
        materials = (
            Equipment.objects.filter(is_consumable=True)
            .values("id", "name", "inventory_number")
            .annotate(total=Sum("quantity_total"), available=Sum("quantity_available"))
            .order_by("name")
        )
        for item in materials:
            writer.writerow(
                [
                    item["name"],
                    item["inventory_number"],
                    item["total"] or 0,
                    item["available"] or 0,
                    usage_by_equipment.get(item["id"], 0),
                ]
            )
        return response

    return HttpResponse("Неизвестный тип отчёта", status=400)


@login_required
def request_create(request):
    if not _can_create_request(request.user):
        return forbidden(request, "Заявки доступны только уполномоченным ролям.")

    if request.method == "POST":
        form = EquipmentRequestForm(request.POST, request.FILES)
        if form.is_valid():
            new_request = form.save(commit=False)
            new_request.requester = request.user
            new_request.status = REQUEST_PENDING
            selected_target_status = (form.cleaned_data.get("non_consumable_target_status") or "").strip()
            restock_non_consumable_action = (form.cleaned_data.get("restock_non_consumable_action") or "").strip()
            if (
                new_request.equipment_id
                and new_request.equipment
                and not new_request.equipment.is_consumable
                and new_request.request_kind != REQUEST_KIND_RESTOCK
                and selected_target_status in {STATUS_REPAIR, STATUS_RETIRED}
            ):
                new_request.non_consumable_target_status = selected_target_status
            else:
                new_request.non_consumable_target_status = ""
            if (
                new_request.request_kind == REQUEST_KIND_RESTOCK
                and new_request.equipment_id
                and new_request.equipment
                and not new_request.equipment.is_consumable
                and restock_non_consumable_action in {RESTOCK_NON_CONSUMABLE_SET_IN_STOCK, RESTOCK_NON_CONSUMABLE_INCREASE}
            ):
                new_request.restock_non_consumable_action = restock_non_consumable_action
            else:
                new_request.restock_non_consumable_action = ""
            new_request._actor = request.user
            new_request.save()
            initial_photo = form.cleaned_data.get("initial_photo")
            if initial_photo:
                EquipmentRequestPhoto.objects.create(
                    request=new_request,
                    image=initial_photo,
                    uploaded_by=request.user,
                )
            messages.success(
                request,
                "Заявка создана и находится на рассмотрении. Ниже список ваших заявок с этим фильтром.",
            )
            return redirect(f"{reverse('request_history')}?{urlencode({'status': REQUEST_PENDING, 'view': 'mine'})}")
    else:
        form = EquipmentRequestForm(initial={"needed_by": timezone.localdate()})
    equipment_photo_map = {}
    equipment_consumable_map = {}
    can_quick_add_request_refs = user_has_capability(request.user, "warehouse_operations")
    equipment_qs = getattr(form.fields.get("equipment"), "queryset", Equipment.objects.none())
    for eq in equipment_qs:
        equipment_consumable_map[str(eq.pk)] = bool(eq.is_consumable)
        if eq.photo:
            equipment_photo_map[str(eq.pk)] = eq.photo.url
    return render(
        request,
        "inventory/request_form.html",
        {
            "form": form,
            "equipment_photo_map": equipment_photo_map,
            "equipment_consumable_map": equipment_consumable_map,
            "can_quick_add_request_refs": can_quick_add_request_refs,
            "workplace_add_url": reverse("portal_create", kwargs={"entity": "workplaces"}),
            "cabinet_add_url": reverse("portal_create", kwargs={"entity": "cabinets"}),
            "equipment_add_url": reverse("portal_create", kwargs={"entity": "equipment"}),
        },
    )


@login_required
def request_detail(request, request_id: int):
    item = get_object_or_404(
        EquipmentRequest.objects.select_related(
            "requester", "equipment", "workplace", "cabinet", "processed_by"
        ),
        pk=request_id,
    )
    can_access = (
        _can_view_all_operational_data(request.user)
        or item.requester_id == request.user.pk
        or user_has_capability(request.user, "users_and_site_admin")
    )
    if not can_access:
        return forbidden(request, "Просмотр этой заявки недоступен.")
    _decorate_request(item)
    mark_equipment_request_thread_read(request.user, item)
    is_approved_locked = item.status == REQUEST_APPROVED

    message_form = EquipmentRequestMessageForm()
    photo_form = EquipmentRequestPhotoForm()
    if request.method == "POST":
        if is_approved_locked:
            messages.error(request, "Одобренная заявка заблокирована для изменений и отправки сообщений.")
            return redirect("request_detail", request_id=item.pk)
        action = request.POST.get("action")
        if action == "add_message":
            message_form = EquipmentRequestMessageForm(request.POST)
            if message_form.is_valid():
                message_obj = message_form.save(commit=False)
                message_obj.request = item
                message_obj.author = request.user
                parent_id = (request.POST.get("parent_id") or "").strip()
                if parent_id.isdigit():
                    parent_message = item.messages.filter(pk=int(parent_id)).first()
                    if parent_message:
                        message_obj.parent = parent_message
                message_obj.save()
                messages.success(request, "Сообщение добавлено.")
                return redirect("request_detail", request_id=item.pk)
        elif action == "add_photo":
            photo_form = EquipmentRequestPhotoForm(request.POST, request.FILES)
            if photo_form.is_valid():
                photo_obj = photo_form.save(commit=False)
                photo_obj.request = item
                photo_obj.uploaded_by = request.user
                photo_obj.save()
                messages.success(request, "Фото добавлено.")
                return redirect("request_detail", request_id=item.pk)
        elif action == "delete_message":
            raw_mid = (request.POST.get("message_id") or "").strip()
            if raw_mid.isdigit():
                msg = EquipmentRequestMessage.objects.filter(
                    pk=int(raw_mid), request_id=item.pk
                ).first()
                if msg:
                    if not _can_delete_specific_request_message(request.user, msg):
                        return forbidden(request, "Удаление чужих сообщений по заявке недоступно.")
                    msg.delete()
                    messages.success(request, "Сообщение удалено.")
                else:
                    messages.error(request, "Сообщение не найдено.")
            else:
                messages.error(request, "Некорректный идентификатор сообщения.")
            return redirect("request_detail", request_id=item.pk)

    threaded_messages = _build_request_message_thread(
        item.messages.select_related("author", "parent").all()
    )
    request_photos = list(item.photos.select_related("uploaded_by").all())
    return render(
        request,
        "inventory/request_detail.html",
        {
            "item": item,
            "messages_list": threaded_messages,
            "photos": request_photos,
            "message_form": message_form,
            "photo_form": photo_form,
            "can_quick_status": _can_process_request_status(request.user) and not is_approved_locked,
            "status_choices": _request_status_choices_without_closed(),
            "can_delete_messages": _can_delete_request_messages(request.user) and not is_approved_locked,
            "can_delete_foreign_messages": (
                _can_delete_request_messages(request.user)
                and not is_approved_locked
                and not user_in_group(request.user, GROUP_SENIOR_TECHNICIAN)
            ),
            "can_add_request_content": not is_approved_locked,
            "is_approved_locked": is_approved_locked,
            "can_update_equipment_condition": bool(
                _can_process_request_status(request.user)
                and item.equipment_id
                and not (item.equipment and item.equipment.is_consumable)
                and not is_approved_locked
            ),
        },
    )


@login_required
@require_POST
def request_update_status(request, request_id: int):
    item = get_object_or_404(EquipmentRequest, pk=request_id)
    if not _can_process_request_status(request.user):
        return forbidden(request, "Быстрая смена статуса недоступна.")
    if item.status == REQUEST_APPROVED:
        messages.error(request, "Одобренная заявка заблокирована для изменений.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("request_history"))
    quick_status = (request.POST.get("quick_status") or "").strip()
    new_status = quick_status or (request.POST.get("status") or "").strip()
    allowed_statuses = {value for value, _ in _request_status_choices_without_closed()}
    if new_status not in allowed_statuses:
        messages.error(request, "Некорректный статус.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("request_history"))
    status_note = (request.POST.get("status_note") or "").strip()
    if new_status != item.status:
        previous_status = item.get_status_display()
        item.status = new_status
        item.processed_by = request.user
        item.processed_at = timezone.now()
        item._actor = request.user
        try:
            item.save(update_fields=["status", "processed_by", "processed_at"])
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect(request.META.get("HTTP_REFERER") or reverse("request_history"))
        note_lines = [f"Статус изменён: {previous_status} -> {item.get_status_display()}."]
        if (
            new_status == REQUEST_APPROVED
            and item.equipment_id
            and item.equipment
            and item.request_kind == REQUEST_KIND_RESTOCK
        ):
            equipment_previous = item.equipment.get_status_display()
            if item.equipment.is_consumable:
                InventoryAdjustment.objects.create(
                    equipment=item.equipment,
                    delta=item.quantity,
                    reason=f"Пополнение по одобренной заявке #{item.pk}",
                    created_by=request.user,
                )
                note_lines.append(
                    f"Пополнение расходника: +{item.quantity} шт. (оборудование #{item.equipment.pk})."
                )
            else:
                # Для пополнения нерасходников всегда увеличиваем количество.
                # Опция "Перевести на склад" дополнительно выставляет статус "На складе".
                action = item.restock_non_consumable_action or RESTOCK_NON_CONSUMABLE_INCREASE
                InventoryAdjustment.objects.create(
                    equipment=item.equipment,
                    delta=item.quantity,
                    reason=f"Пополнение нерасходника по одобренной заявке #{item.pk}",
                    created_by=request.user,
                )
                note_lines.append(
                    f"Пополнение нерасходника: +{item.quantity} шт. (оборудование #{item.equipment.pk})."
                )
                if action == RESTOCK_NON_CONSUMABLE_SET_IN_STOCK:
                    if item.equipment.status != STATUS_IN_STOCK:
                        item.equipment.status = STATUS_IN_STOCK
                        item.equipment._actor = request.user
                        item.equipment.save(update_fields=["status"])
                    note_lines.append(
                        f"Нерасходник переведён на склад: {equipment_previous} -> {item.equipment.get_status_display()} "
                        f"(оборудование #{item.equipment.pk})."
                    )
        elif (
            new_status == REQUEST_APPROVED
            and item.equipment_id
            and item.equipment
            and not item.equipment.is_consumable
            and item.non_consumable_target_status in {STATUS_REPAIR, STATUS_RETIRED}
        ):
            equipment_previous = item.equipment.get_status_display()
            if item.equipment.status != item.non_consumable_target_status:
                item.equipment.status = item.non_consumable_target_status
                item.equipment._actor = request.user
                item.equipment.save(update_fields=["status"])
            note_lines.append(
                f"Состояние оборудования изменено после одобрения: "
                f"{equipment_previous} -> {item.equipment.get_status_display()} (оборудование #{item.equipment.pk})."
            )
        elif (
            new_status == REQUEST_APPROVED
            and item.equipment_id
            and item.equipment
            and item.request_kind == REQUEST_KIND_WRITEOFF
            and not item.equipment.is_consumable
        ):
            equipment_previous = item.equipment.get_status_display()
            if item.equipment.status != STATUS_RETIRED:
                item.equipment.status = STATUS_RETIRED
                item.equipment.quantity_available = 0
                item.equipment._actor = request.user
                item.equipment.save(update_fields=["status", "quantity_available"])
            note_lines.append(
                f"Оборудование списано после одобрения: {equipment_previous} -> {item.equipment.get_status_display()} "
                f"(оборудование #{item.equipment.pk})."
            )
        if status_note:
            note_lines.append(status_note)
        EquipmentRequestMessage.objects.create(
            request=item,
            author=request.user,
            body="\n".join(note_lines),
        )
        messages.success(request, "Статус обновлён.")
    elif status_note:
        EquipmentRequestMessage.objects.create(
            request=item,
            author=request.user,
            body=status_note,
        )
        messages.success(request, "Комментарий добавлен.")
    return redirect(request.META.get("HTTP_REFERER") or reverse("request_history"))


@login_required
@require_POST
def request_update_equipment_condition(request, request_id: int):
    item = get_object_or_404(EquipmentRequest.objects.select_related("equipment"), pk=request_id)
    if not _can_process_request_status(request.user):
        return forbidden(request, "Смена состояния оборудования доступна только обработчикам заявок.")
    if item.status == REQUEST_APPROVED:
        messages.error(request, "Одобренная заявка заблокирована для изменений.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("request_detail", kwargs={"request_id": item.pk}))
    if not item.equipment_id:
        messages.error(request, "В заявке не указано оборудование.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("request_detail", kwargs={"request_id": item.pk}))
    if item.equipment and item.equipment.is_consumable:
        messages.error(request, "Для расходников используйте одобрение заявки: количество уменьшается автоматически.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("request_detail", kwargs={"request_id": item.pk}))

    action = (request.POST.get("equipment_condition") or "").strip()
    target_status = (
        STATUS_REPAIR if action == "repair"
        else STATUS_RETIRED if action == "retired"
        else "in_stock" if action in {"in_stock", "stock"}
        else None
    )
    if target_status is None:
        messages.error(request, "Некорректное действие для оборудования.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("request_detail", kwargs={"request_id": item.pk}))

    equipment = item.equipment
    if equipment is None:
        messages.error(request, "Оборудование не найдено.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("request_detail", kwargs={"request_id": item.pk}))
    back_url = request.META.get("HTTP_REFERER") or reverse("request_detail", kwargs={"request_id": item.pk})
    raw_qty = (request.POST.get("equipment_condition_qty") or "").strip()
    unit_serial = (request.POST.get("equipment_condition_unit_serial") or "").strip()
    partial_mode = bool(raw_qty or unit_serial)
    if partial_mode:
        if equipment.quantity_total <= 1:
            messages.error(
                request,
                "Для частичного перевода нужно, чтобы в позиции было больше одной единицы.",
            )
            return redirect(back_url)
        if not raw_qty.isdigit():
            messages.error(request, "Укажите корректное количество для частичного перевода.")
            return redirect(back_url)
        qty = int(raw_qty)
        if qty <= 0:
            messages.error(request, "Количество должно быть не меньше 1.")
            return redirect(back_url)
        if qty >= equipment.quantity_total:
            messages.error(
                request,
                "Для перевода всей позиции используйте обычную кнопку статуса без количества.",
            )
            return redirect(back_url)
        if qty > equipment.quantity_available:
            messages.error(request, "Нельзя выделить больше единиц, чем сейчас доступно на складе.")
            return redirect(back_url)
        if not unit_serial:
            base = (equipment.inventory_number or f"EQ-{equipment.pk}").strip().replace(" ", "-")
            for _ in range(8):
                candidate = f"{base}-split-{secrets.token_hex(2)}"
                if not Equipment.all_objects.filter(inventory_number=candidate).exists():
                    unit_serial = candidate
                    break
            if not unit_serial:
                messages.error(request, "Не удалось автоматически подобрать уникальный номер новой позиции.")
                return redirect(back_url)
        if Equipment.all_objects.filter(inventory_number=unit_serial).exists():
            messages.error(request, "Позиция с таким номером уже есть в системе.")
            return redirect(back_url)
        try:
            with transaction.atomic():
                updated = Equipment.objects.filter(
                    pk=equipment.pk,
                    quantity_available__gte=qty,
                    quantity_total__gt=qty,
                ).update(
                    quantity_total=F("quantity_total") - qty,
                    quantity_available=F("quantity_available") - qty,
                )
                if updated != 1:
                    messages.error(
                        request,
                        "Не удалось обновить остатки — возможно, позицию уже изменили. Обновите страницу.",
                    )
                    return redirect(back_url)
                moved_row = Equipment(
                    name=equipment.name,
                    inventory_number=unit_serial,
                    serial_number=unit_serial,
                    category=equipment.category,
                    workplace=equipment.workplace,
                    model=equipment.model,
                    is_consumable=False,
                    status=target_status,
                    quantity_total=qty,
                    quantity_available=qty if target_status == STATUS_REPAIR else 0,
                    low_stock_threshold=0,
                    purchase_date=equipment.purchase_date,
                    warranty_end=equipment.warranty_end,
                    notes=(
                        f"Выделено из позиции #{equipment.pk} ({equipment.inventory_number}) по заявке #{item.pk}. "
                        f"Действие: "
                        f"{'ремонт' if target_status == STATUS_REPAIR else 'списание из-за поломки' if target_status == STATUS_RETIRED else 'перевод на склад'}, "
                        f"{qty} шт."
                    ),
                )
                moved_row._actor = request.user
                moved_row.save()
        except Exception:
            logger.exception("request_update_equipment_condition split failed for request_id=%s", item.pk)
            messages.error(request, "Не удалось выполнить частичный перевод оборудования.")
            return redirect(back_url)
        EquipmentRequestMessage.objects.create(
            request=item,
            author=request.user,
            body=(
                f"Частичный перевод оборудования #{equipment.pk}: выделено {qty} шт. в статус "
                f"«{moved_row.get_status_display()}», новая позиция #{moved_row.pk}."
            ),
        )
        messages.success(request, f"Создана отдельная позиция #{moved_row.pk} ({qty} шт.).")
        return redirect(back_url)

    previous_status_display = equipment.get_status_display()
    equipment.status = target_status
    equipment._actor = request.user
    equipment.save(update_fields=["status"])
    EquipmentRequestMessage.objects.create(
        request=item,
        author=request.user,
        body=(
            f"Состояние оборудования изменено: {previous_status_display} -> {equipment.get_status_display()} "
            f"(оборудование #{equipment.pk})."
        ),
    )
    messages.success(request, "Состояние оборудования обновлено.")
    return redirect(back_url)


@login_required
def usage_create(request):
    return forbidden(request, "Раздел отключён. Расход расходников учитывается при одобрении заявки.")


@login_required
def adjustment_create(request):
    if not (user_in_group(request.user, GROUP_ADMIN) or user_in_group(request.user, GROUP_WAREHOUSE)):
        return forbidden(request, "Корректировки доступны только уполномоченным ролям.")

    if request.method == "POST":
        form = InventoryAdjustmentForm(request.POST)
        if form.is_valid():
            adjustment = form.save(commit=False)
            adjustment.created_by = request.user
            adjustment._actor = request.user
            adjustment.save()
            messages.success(request, "Корректировка остатка сохранена.")
            return redirect("equipment_list")
    else:
        form = InventoryAdjustmentForm(initial={"delta": 1})

    return render(request, "inventory/adjustment_form.html", {"form": form})


@login_required
def timer_create(request):
    return forbidden(request, "Раздел таймеров отключён.")


@login_required
def timer_quick_start(request):
    return forbidden(request, "Раздел таймеров отключён.")


@login_required
def timer_stop(request, timer_id: int):
    return forbidden(request, "Раздел таймеров отключён.")


@login_required
def checkout_create(request):
    return forbidden(request, "Форма выдач отключена. Оформляйте расход через заявки.")


@login_required
@require_POST
def checkout_return(request, checkout_id: int):
    return forbidden(request, "Возвраты отключены вместе с разделом выдач.")


@login_required
def user_preferences_view(request):
    preferences = _get_user_preferences(request.user)
    current_language = getattr(request, "LANGUAGE_CODE", None) or getattr(request, "LANGUAGE_CODE", "ru") or "ru"
    if request.method == "POST":
        form = UserPreferenceForm(request.POST, instance=preferences, language_code=current_language)
        if form.is_valid():
            saved = form.save()
            if saved.preferred_language:
                translation.activate(saved.preferred_language)
                request.LANGUAGE_CODE = saved.preferred_language
                request.session["django_language"] = saved.preferred_language
            messages.success(request, "Настройки сохранены.")
            return redirect("user_preferences")
    else:
        form = UserPreferenceForm(instance=preferences, language_code=current_language)

    return render(request, "inventory/user_preferences.html", {"form": form})


@login_required
def api_docs(request):
    return render(
        request,
        "inventory/api_docs.html",
        {
            "endpoints": [
                "/api/v1/auth/token/",
                "/api/v1/auth/token/revoke/",
                "/api/v1/equipment/",
                "/api/v1/workplaces/",
                "/api/v1/cabinets/",
                "/api/v1/categories/",
                "/api/v1/requests/",
                "/api/v1/usage/",
                "/api/v1/adjustments/",
                "/api/v1/checkouts/",
            ]
        },
    )


@login_required
def api_token_view(request):
    if not user_has_capability(request.user, "users_and_site_admin"):
        return forbidden(request, "Управление API-токеном доступно только администраторам.")
    token_obj = Token.objects.filter(user=request.user).first()
    token_value = token_obj.key if token_obj else ""
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "revoke":
            Token.objects.filter(user=request.user).delete()
            token_value = ""
            messages.success(request, "API токен отозван.")
            return redirect("api_token")
        token_obj, _created = Token.objects.get_or_create(user=request.user)
        token_value = token_obj.key
        messages.success(request, "API токен готов. Скопируйте и используйте в Authorization: Token <key>.")
    return render(
        request,
        "inventory/api_token.html",
        {
            "token_value": token_value,
            "has_token": bool(token_value),
        },
    )


@login_required
def about_site(request):
    return render(
        request,
        "inventory/about_site.html",
        {
            "yandex_maps_terms_url": "https://yandex.ru/legal/maps_api/ru/",
            "yandex_maps_service_terms_url": "https://yandex.ru/legal/maps_termsofuse/",
        },
    )


@login_required
def quality_report_view(request):
    if not _can_access_quality_report(request.user):
        return forbidden(request, "Результаты проверок доступны только администратору и системному администратору.")

    report = load_quality_report()
    if request.method == "POST":
        report = generate_quality_report()
        messages.success(request, "Отчёт по качеству обновлён.")
        return redirect("quality_report")

    return render(request, "inventory/quality_report.html", {"report": report})


@login_required
def notifications_view(request):
    dm_unread_total = unread_direct_message_count(request.user)
    conversations = _message_conversation_summaries(request.user)
    dm_with_unread = [c for c in conversations if c["unread_count"] > 0]
    request_groups = unread_request_notification_groups(request.user)
    return render(
        request,
        "inventory/notifications.html",
        {
            "dm_unread_total": dm_unread_total,
            "dm_conversations_unread": dm_with_unread,
            "request_notification_groups": request_groups,
        },
    )


@login_required
def direct_messages_view(request):
    is_dm_moderator = user_has_capability(request.user, "users_and_site_admin")
    dm_moderation_mode = bool(is_dm_moderator and request.GET.get("moderation") == "1")

    if request.method == "POST" and request.POST.get("action") == "delete_direct_message":
        if not is_dm_moderator:
            return forbidden(request, "Удаление сообщений доступно только администраторам.")
        mid = (request.POST.get("message_id") or "").strip()
        if not mid.isdigit():
            messages.error(request, "Не удалось определить сообщение.")
            return redirect(request.POST.get("next") or reverse("direct_messages"))
        dm = get_object_or_404(DirectMessage, pk=int(mid))
        dm.delete()
        messages.success(request, "Сообщение удалено.")
        return redirect(request.POST.get("next") or reverse("direct_messages"))

    selected_user = None
    selected_mod_pair = None
    selected_user_id = request.GET.get("user") or request.POST.get("recipient")
    if selected_user_id:
        selected_user = get_object_or_404(User.objects.filter(is_active=True), pk=selected_user_id)
        if selected_user.pk == request.user.pk:
            selected_user = None

    if request.method == "POST":
        form = DirectMessageForm(request.POST, sender=request.user)
        if form.is_valid():
            message_obj = form.save(commit=False)
            message_obj.sender = request.user
            message_obj.save()
            messages.success(request, "Сообщение отправлено.")
            return redirect(f"{reverse('direct_messages')}?user={message_obj.recipient_id}")
    else:
        form = DirectMessageForm(
            sender=request.user,
            initial={"recipient": selected_user.pk} if selected_user else None,
        )

    conversation_messages = []
    if selected_user is not None:
        DirectMessage.objects.filter(
            sender=selected_user,
            recipient=request.user,
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        conversation_messages = (
            DirectMessage.objects.filter(
                (Q(sender=request.user) & Q(recipient=selected_user))
                | (Q(sender=selected_user) & Q(recipient=request.user))
            )
            .select_related("sender", "recipient")
            .order_by("created_at", "id")
        )

    conversations = _message_conversation_summaries(request.user)
    if selected_user is None and conversations and not dm_moderation_mode:
        selected_user = conversations[0]["user"]
        form = DirectMessageForm(sender=request.user, initial={"recipient": selected_user.pk})
        conversation_messages = (
            DirectMessage.objects.filter(
                (Q(sender=request.user) & Q(recipient=selected_user))
                | (Q(sender=selected_user) & Q(recipient=request.user))
            )
            .select_related("sender", "recipient")
            .order_by("created_at", "id")
        )

    moderation_conversations = []
    moderation_messages = []
    if dm_moderation_mode:
        raw_a = (request.GET.get("a") or "").strip()
        raw_b = (request.GET.get("b") or "").strip()
        selected_a = int(raw_a) if raw_a.isdigit() else None
        selected_b = int(raw_b) if raw_b.isdigit() else None

        # Ограничиваем расчёт «всех диалогов» последними сообщениями,
        # чтобы страница модерации оставалась быстрой.
        all_direct_messages = list(
            DirectMessage.objects.select_related("sender", "recipient")
            .order_by("-created_at", "-id")[:4000]
        )
        by_pair = {}
        for row in all_direct_messages:
            a_id, b_id = sorted((row.sender_id, row.recipient_id))
            rec = by_pair.get((a_id, b_id))
            if rec is None:
                user_a = row.sender if row.sender_id == a_id else row.recipient
                user_b = row.recipient if row.recipient_id == b_id else row.sender
                rec = {
                    "a_id": a_id,
                    "b_id": b_id,
                    "user_a": user_a,
                    "user_b": user_b,
                    "last_message": row.body,
                    "last_at": row.created_at,
                    "count": 0,
                }
                by_pair[(a_id, b_id)] = rec
            rec["count"] += 1
        moderation_conversations = sorted(
            by_pair.values(),
            key=lambda x: x["last_at"],
            reverse=True,
        )[:500]
        if moderation_conversations:
            if selected_a is None or selected_b is None:
                selected_mod_pair = (
                    moderation_conversations[0]["a_id"],
                    moderation_conversations[0]["b_id"],
                )
            else:
                selected_mod_pair = tuple(sorted((selected_a, selected_b)))
            moderation_messages = list(
                DirectMessage.objects.select_related("sender", "recipient")
                .filter(
                    (
                        Q(sender_id=selected_mod_pair[0], recipient_id=selected_mod_pair[1])
                        | Q(sender_id=selected_mod_pair[1], recipient_id=selected_mod_pair[0])
                    )
                )
                .order_by("created_at", "id")
            )

    return render(
        request,
        "inventory/direct_messages.html",
        {
            "conversations": conversations,
            "selected_user": selected_user,
            "conversation_messages": conversation_messages,
            "form": form,
            "is_dm_moderator": is_dm_moderator,
            "dm_moderation_mode": dm_moderation_mode,
            "moderation_conversations": moderation_conversations,
            "moderation_messages": moderation_messages,
            "selected_mod_pair": selected_mod_pair,
        },
    )


@login_required
def role_assignment(request):
    if not user_has_capability(request.user, "users_and_site_admin"):
        return forbidden(request, "Выдача ролей доступна только администратору.")

    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLE_DESCRIPTIONS}

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        role_name = (request.POST.get("role") or "").strip()
        target_user = get_object_or_404(User, pk=user_id)
        redirect_query = {}
        query = (request.POST.get("q") or "").strip()
        role_filter = (request.POST.get("role_filter") or "").strip()
        if query:
            redirect_query["q"] = query
        if role_filter:
            redirect_query["role"] = role_filter
        if role_name in groups:
            target_user.groups.clear()
            target_user.groups.add(groups[role_name])
            messages.success(request, f"Роль пользователя {target_user.username} обновлена.")
        elif role_name == "":
            target_user.groups.clear()
            messages.success(request, f"У пользователя {target_user.username} роль снята.")
        redirect_url = reverse("role_assignment")
        if redirect_query:
            redirect_url = f"{redirect_url}?{urlencode(redirect_query)}"
        return redirect(redirect_url)

    users = User.objects.prefetch_related("groups").all().order_by("username")
    query = request.GET.get("q", "").strip()
    selected_role = request.GET.get("role", "").strip()
    user_role_map = {}
    for item in users:
        if item.is_superuser:
            user_role_map[item.pk] = GROUP_SYSADMIN
            continue
        current_names = {group.name for group in item.groups.all()}
        resolved = ""
        for canonical_name in ROLE_DESCRIPTIONS:
            all_names = {canonical_name}
            all_names.update(ROLE_ALIASES.get(canonical_name, set()))
            if current_names & all_names:
                resolved = canonical_name
                break
        user_role_map[item.pk] = resolved
    if query:
        query_lower = query.lower()
        users = [
            item for item in users
            if query_lower in item.username.lower()
            or query_lower in item.email.lower()
            or query_lower in f"{item.first_name} {item.last_name}".strip().lower()
        ]
    if selected_role == "__without_role__":
        users = [item for item in users if not user_role_map.get(item.pk)]
    elif selected_role:
        users = [item for item in users if user_role_map.get(item.pk) == selected_role]
    role_counts = {role_name: 0 for role_name in ROLE_DESCRIPTIONS}
    role_capability_map = {
        role_name: [ROLE_CAPABILITY_LABELS[item] for item in spec.capabilities]
        for role_name, spec in ROLE_SPECS.items()
    }
    without_role_count = 0
    for user_obj in User.objects.prefetch_related("groups").all():
        role_name = user_role_map.get(user_obj.pk, "")
        if role_name:
            role_counts[role_name] = role_counts.get(role_name, 0) + 1
        else:
            without_role_count += 1
    return render(
        request,
        "inventory/role_assignment.html",
        {
            "users": users,
            "roles": ROLE_DESCRIPTIONS,
            "user_role_map": user_role_map,
            "filters": {"q": query, "role": selected_role},
            "role_counts": role_counts,
            "role_capability_map": role_capability_map,
            "without_role_count": without_role_count,
        },
    )


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_default_landing_url(request.user))
    if request.method == "POST":
        form = RussianAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(_default_landing_url(user))
    else:
        form = RussianAuthenticationForm(request)
    return render(request, "inventory/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("login")


def register_view(request):
    if request.user.is_authenticated:
        return redirect(_default_landing_url(request.user))
    if request.method == "POST":
        form = RussianUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            default_group, _ = Group.objects.get_or_create(name=GROUP_TECHNICIAN)
            user.groups.add(default_group)
            schedule_type = form.cleaned_data.get("schedule_type") or EmployeeSchedule.SCHEDULE_5_2
            custom_weekdays = form.cleaned_data.get("custom_weekdays") or []
            custom_workdays = ",".join(sorted(set(custom_weekdays))) if custom_weekdays else "0,1,2,3,4"
            EmployeeSchedule.objects.get_or_create(
                user=user,
                defaults={
                    "schedule_type": schedule_type,
                    "custom_workdays": custom_workdays,
                    "is_active": True,
                },
            )
            return redirect("login")
    else:
        form = RussianUserCreationForm()
    return render(request, "inventory/register.html", {"form": form, "registration_domains": get_registration_email_domains()})


def password_reset_request_view(request):
    if request.method == "POST":
        form = PasswordResetRequestForm(request.POST, user=request.user if request.user.is_authenticated else None)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            if request.user.is_authenticated:
                user = request.user
                if not (user.email or "").strip():
                    user.email = email
                    user.save(update_fields=["email"])
            else:
                user = User.objects.filter(is_active=True, email__iexact=email).first()
            if user:
                code = _generate_password_reset_code()
                expires_at = timezone.now() + timedelta(minutes=PASSWORD_RESET_CODE_TTL_MINUTES)
                try:
                    _send_password_reset_email(request=request, user=user, to_email=email, code=code)
                except _PASSWORD_RESET_MAIL_ERRORS:
                    logger.exception("Password reset email failed for %s", email)
                    form.add_error(
                        None,
                        "Не удалось отправить письмо. Проверьте настройки почты (SMTP или провайдера) и попробуйте снова.",
                    )
                except Exception:
                    logger.exception("Password reset email unexpected error for %s", email)
                    form.add_error(
                        None,
                        "Не удалось отправить письмо. Проверьте настройки почты (SMTP или провайдера) и попробуйте снова.",
                    )
                else:
                    logger.info("Password reset email sent to %s (user_id=%s)", email, user.pk)
                    PasswordResetCode.objects.filter(user=user, email__iexact=email, used_at__isnull=True).update(
                        used_at=timezone.now()
                    )
                    PasswordResetCode.objects.create(
                        user=user,
                        email=email,
                        code_hash=_password_reset_code_hash(email, code),
                        expires_at=expires_at,
                    )

            if not form.errors:
                messages.success(
                    request,
                    "Если этот адрес есть у учётной записи, на него отправлен код (см. подсказку на странице ввода кода). "
                    + _password_reset_success_hint(),
                )
                return redirect("password_reset_confirm")
    else:
        initial = {}
        if request.user.is_authenticated and (request.user.email or "").strip():
            initial["email"] = request.user.email.strip()
        form = PasswordResetRequestForm(user=request.user if request.user.is_authenticated else None, initial=initial)

    return render(
        request,
        "inventory/password_reset_request.html",
        {
            "form": form,
            "password_reset_needs_email": request.user.is_authenticated and not (request.user.email or "").strip(),
            "password_reset_delivery_hint": _password_reset_delivery_hint(),
            "registration_domains": get_registration_email_domains(),
        },
    )


def password_reset_confirm_view(request):
    if request.method == "POST":
        form = PasswordResetConfirmForm(
            request.POST,
            user=request.user if request.user.is_authenticated else None,
        )
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            code = form.cleaned_data["code"]
            reset_entry = (
                PasswordResetCode.objects.select_related("user")
                .filter(email__iexact=email, used_at__isnull=True, expires_at__gte=timezone.now())
                .order_by("-created_at", "-id")
                .first()
            )
            if not reset_entry or reset_entry.code_hash != _password_reset_code_hash(email, code):
                form.add_error("code", "Неверный или просроченный код.")
            elif request.user.is_authenticated and reset_entry.user_id != request.user.pk:
                form.add_error("code", "Этот код относится к другой учётной записи.")
            else:
                user = reset_entry.user
                user.set_password(form.cleaned_data["new_password1"])
                user.save(update_fields=["password"])
                reset_entry.used_at = timezone.now()
                reset_entry.save(update_fields=["used_at"])
                PasswordResetCode.objects.filter(
                    user=user,
                    email__iexact=email,
                    used_at__isnull=True,
                ).exclude(pk=reset_entry.pk).update(used_at=timezone.now())
                if request.user.is_authenticated:
                    messages.success(request, "Пароль обновлён.")
                    return redirect("user_preferences")
                messages.success(request, "Пароль обновлён. Теперь можно войти.")
                return redirect("login")
    else:
        initial = {}
        if request.user.is_authenticated and (request.user.email or "").strip():
            initial["email"] = request.user.email.strip()
        form = PasswordResetConfirmForm(
            initial=initial,
            user=request.user if request.user.is_authenticated else None,
        )

    return render(
        request,
        "inventory/password_reset_confirm.html",
        {
            "form": form,
            "password_reset_delivery_hint": _password_reset_delivery_hint(),
            "registration_domains": get_registration_email_domains(),
        },
    )


@login_required
def toggle_show_deleted(request):
    if request.method == "POST":
        request.session["show_deleted_global"] = request.POST.get("show_deleted_global") == "1"
        request.session.modified = True
    back = request.META.get("HTTP_REFERER") or reverse("analytics")
    return redirect(back)


def _data_tools_context():
    db_cfg = settings.DATABASES["default"]
    engine = db_cfg["ENGINE"]
    sqlite_engine = engine.endswith("sqlite3")
    postgresql_engine = engine.endswith("postgresql")
    return {
        "sqlite_available": sqlite_engine,
        "postgresql_available": postgresql_engine,
        "db_name": db_cfg.get("NAME", ""),
        "db_host": db_cfg.get("HOST", "localhost"),
        "db_port": db_cfg.get("PORT", "5432"),
        "db_user": db_cfg.get("USER", "postgres"),
        "backup_import_form": BackupImportForm(),
        "postgresql_dump_import_form": PostgresqlDumpImportForm(),
        "can_import_backup": False,
    }


@login_required
def data_tools(request):
    if not _can_access_data_tools(request.user):
        return forbidden(request, "Инструменты данных доступны только администратору и системному администратору.")
    return render(
        request,
        "inventory/tools/data_io.html",
        {**_data_tools_context(), "can_import_backup": _can_import_backup(request.user)},
    )


@login_required
def import_json_backup(request):
    if not _can_import_backup(request.user):
        return forbidden(request, "Импорт доступен только администратору.")
    if request.method != "POST":
        return redirect("data_tools")

    form = BackupImportForm(request.POST, request.FILES)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return render(
            request,
            "inventory/tools/data_io.html",
            {**_data_tools_context(), "backup_import_form": form, "can_import_backup": _can_import_backup(request.user)},
            status=400,
        )

    temp_path = None
    try:
        uploaded_file = form.cleaned_data["backup_file"]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as temp_file:
            for chunk in uploaded_file.chunks():
                temp_file.write(chunk)
            temp_path = temp_file.name

        call_command("loaddata", temp_path, verbosity=0)
        messages.success(request, f'Резервная копия "{uploaded_file.name}" успешно импортирована.')
    except Exception as exc:
        messages.error(request, f"Ошибка импорта резервной копии: {exc}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

    return redirect("data_tools")


@login_required
def download_json_backup(request):
    if not _can_access_data_tools(request.user):
        return forbidden(request, "Экспорт доступен только администратору и системному администратору.")
    out = io.StringIO()
    call_command("dumpdata", indent=2, stdout=out)
    response = HttpResponse(out.getvalue(), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="backup.json"'
    return response


@login_required
def download_sqlite_backup(request):
    if not _can_access_data_tools(request.user):
        return forbidden(request, "Экспорт доступен только администратору и системному администратору.")
    db_cfg = settings.DATABASES["default"]
    if not db_cfg["ENGINE"].endswith("sqlite3"):
        return HttpResponse("Резервная копия SQLite доступна только при движке sqlite3.", status=400)
    db_path = Path(str(db_cfg["NAME"]))
    if not db_path.exists():
        return HttpResponse("Файл SQLite не найден.", status=404)
    return FileResponse(db_path.open("rb"), as_attachment=True, filename=db_path.name)


@login_required
def download_postgresql_backup(request):
    if not _can_access_data_tools(request.user):
        return forbidden(request, "Экспорт доступен только администратору и системному администратору.")
    db_cfg = settings.DATABASES["default"]
    if not str(db_cfg["ENGINE"]).endswith("postgresql"):
        return HttpResponse(
            "Резервная копия PostgreSQL доступна только при подключении к PostgreSQL.",
            status=400,
            content_type="text/plain; charset=utf-8",
        )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config = get_postgresql_backup_config(output_dir=tmp, keep_count=99)
            result = create_postgresql_backup(config, label="web")
            data = result.backup_path.read_bytes()
            filename = result.backup_path.name
    except CommandError as exc:
        messages.error(request, str(exc))
        return redirect("data_tools")
    response = HttpResponse(data, content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def import_postgresql_dump(request):
    if not _can_import_backup(request.user):
        return forbidden(request, "Импорт дампа PostgreSQL доступен только администратору.")
    if request.method != "POST":
        return redirect("data_tools")
    db_cfg = settings.DATABASES["default"]
    if not str(db_cfg["ENGINE"]).endswith("postgresql"):
        messages.error(request, "Импорт дампа доступен только для PostgreSQL.")
        return redirect("data_tools")

    form = PostgresqlDumpImportForm(request.POST, request.FILES)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return render(
            request,
            "inventory/tools/data_io.html",
            {**_data_tools_context(), "postgresql_dump_import_form": form, "can_import_backup": _can_import_backup(request.user)},
            status=400,
        )

    temp_path = None
    try:
        uploaded = form.cleaned_data["dump_file"]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dump") as tmp:
            for chunk in uploaded.chunks():
                tmp.write(chunk)
            temp_path = Path(tmp.name)
        config = get_postgresql_backup_config()
        restore_postgresql_custom_dump(temp_path, config)
        messages.success(request, f"Дамп «{uploaded.name}» восстановлен в текущую базу.")
    except CommandError as exc:
        messages.error(request, str(exc))
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return redirect("data_tools")


@login_required
def export_portal_logs_csv(request):
    if not user_in_group(request.user, GROUP_ADMIN):
        return forbidden(request, "Экспорт доступен только администратору.")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="portal-actions.csv"'
    writer = csv.writer(response)
    writer.writerow(["created_at", "user", "action", "entity", "object", "path"])
    for log in AdminPortalLog.objects.select_related("actor").order_by("-created_at")[:5000]:
        writer.writerow(
            [
                log.created_at.isoformat(),
                log.actor.get_username() if log.actor else "",
                log.action,
                log.entity_slug,
                log.object_repr,
                log.path,
            ]
        )
    return response


@login_required
def equipment_qr(request, equipment_id: int):
    item = get_object_or_404(Equipment, pk=equipment_id)
    target_url = request.build_absolute_uri(reverse("equipment_public_card", kwargs={"equipment_id": item.pk}))
    img = qrcode.make(target_url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return HttpResponse(buf.getvalue(), content_type="image/png")
