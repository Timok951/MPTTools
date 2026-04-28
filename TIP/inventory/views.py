import csv
from datetime import timedelta
import hashlib
import io
import os
from pathlib import Path
import random
import tempfile
from urllib.parse import urlencode

import qrcode

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.conf import settings
from django.core.mail import send_mail
from django.views.decorators.http import require_POST
from django.utils import translation
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.db.utils import OperationalError, ProgrammingError
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from assets.models import Equipment, EquipmentCheckout
from audit.models import AdminPortalLog, AuditLog
from core.models import (
    Cabinet,
    DirectMessage,
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
    REQUEST_APPROVED,
    REQUEST_CLOSED,
    REQUEST_ISSUED,
    REQUEST_REJECTED,
    MaterialUsage,
    REQUEST_PENDING,
)
from .authz import (
    GROUP_ADMIN,
    GROUP_BUILDER,
    GROUP_FIRST_LINE_SUPPORT,
    ROLE_CAPABILITY_LABELS,
    ROLE_ALIASES,
    ROLE_SPECS,
    GROUP_SENIOR_TECHNICIAN,
    GROUP_SYSADMIN,
    GROUP_WAREHOUSE,
    user_has_capability,
    user_in_group,
)
from .forms import (
    BackupImportForm,
    DirectMessageForm,
    EquipmentCheckoutForm,
    EquipmentRequestMessageForm,
    EquipmentRequestPhotoForm,
    EquipmentRequestForm,
    InventoryAdjustmentForm,
    PasswordResetConfirmForm,
    PasswordResetRequestForm,
    MaterialUsageForm,
    RussianAuthenticationForm,
    RussianUserCreationForm,
    UserPreferenceForm,
)
from .quality_report import generate_quality_report, load_quality_report

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
            {"value": REQUEST_ISSUED, "label": "Отметить как выданную"},
            {"value": REQUEST_REJECTED, "label": "Отклонить"},
            {"value": REQUEST_CLOSED, "label": "Закрыть"},
        ],
    },
    REQUEST_ISSUED: {
        "badge_class": "badge badge-issued",
        "quick_actions": [
            {"value": REQUEST_CLOSED, "label": "Закрыть"},
        ],
    },
    REQUEST_REJECTED: {
        "badge_class": "badge badge-rejected",
        "quick_actions": [
            {"value": REQUEST_PENDING, "label": "Вернуть на рассмотрение"},
        ],
    },
    REQUEST_CLOSED: {
        "badge_class": "badge badge-closed",
        "quick_actions": [
            {"value": REQUEST_PENDING, "label": "Переоткрыть"},
        ],
    },
}

EQUIPMENT_STATUS_HELPERS = {
    "in_stock": {"badge_class": "badge badge-approved"},
    "assigned": {"badge_class": "badge badge-issued"},
    "checked_out": {"badge_class": "badge badge-pending"},
    "repair": {"badge_class": "badge badge-rejected"},
    "retired": {"badge_class": "badge badge-closed"},
}

VISIBLE_EQUIPMENT_STATUSES = ("in_stock", "repair", "retired")


def _can_manage_timers(user) -> bool:
    return False


def _can_view_all_operational_data(user) -> bool:
    return user_has_capability(user, "warehouse_operations") or user_has_capability(user, "request_processing")


def _can_access_history(user) -> bool:
    return user_has_capability(user, "users_and_site_admin")


def _can_access_reports(user) -> bool:
    return user_has_capability(user, "report_access")


def _can_access_data_tools(user) -> bool:
    return user_has_capability(user, "data_tools_access")


def _can_import_backup(user) -> bool:
    return user_has_capability(user, "users_and_site_admin")


def _can_access_quality_report(user) -> bool:
    return user_has_capability(user, "quality_access")


def _can_create_request(user) -> bool:
    return user_has_capability(user, "request_creation")


def _can_process_request_status(user) -> bool:
    return user_has_capability(user, "request_processing")


def _can_create_checkout(user) -> bool:
    return False


def _can_create_usage(user) -> bool:
    return user_has_capability(user, "usage_writeoff") or user_has_capability(user, "warehouse_operations")


def _can_return_checkout(user, checkout: EquipmentCheckout) -> bool:
    return (
        user_has_capability(user, "warehouse_operations")
        or checkout.taken_by_id == user.pk
    )


def _decorate_request(item: EquipmentRequest):
    helper = REQUEST_STATUS_HELPERS.get(item.status, {})
    item.badge_class = helper.get("badge_class", "badge")
    item.quick_actions = helper.get("quick_actions", [])
    return item


def _decorate_equipment(item: Equipment):
    helper = EQUIPMENT_STATUS_HELPERS.get(item.status, {})
    item.status_badge_class = helper.get("badge_class", "badge")
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


def _password_reset_code_hash(email: str, code: str) -> str:
    normalized_email = (email or "").strip().lower()
    return hashlib.sha256(f"{normalized_email}:{code}".encode("utf-8")).hexdigest()


def _generate_password_reset_code() -> str:
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def forbidden(request, message: str):
    back_url = request.META.get("HTTP_REFERER") or reverse("analytics")
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


@login_required
def analytics(request):
    if not user_in_group(request.user, GROUP_ADMIN):
        return forbidden(request, "Аналитика доступна только администратору.")

    equipment_total = Equipment.objects.count()
    consumables_total = Equipment.objects.filter(is_consumable=True).count()
    low_stock_total = Equipment.objects.filter(quantity_available__lte=F("low_stock_threshold")).count()
    requests_pending = EquipmentRequest.objects.filter(status=REQUEST_PENDING).count()
    active_checkouts = EquipmentCheckout.objects.filter(returned_at__isnull=True).count()

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

    recent_requests = EquipmentRequest.objects.select_related("requester", "equipment").order_by("-requested_at")[:10]
    recent_usage = MaterialUsage.objects.select_related("used_by", "equipment").order_by("-used_at")[:10]

    start_date = timezone.now().date() - timedelta(days=29)
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
    usage_daily_qs = (
        MaterialUsage.objects.filter(used_at__date__gte=start_date)
        .annotate(day=TruncDate("used_at"))
        .values("day")
        .annotate(count=Count("id"))
    )
    checkout_daily_qs = (
        EquipmentCheckout.objects.filter(taken_at__date__gte=start_date)
        .annotate(day=TruncDate("taken_at"))
        .values("day")
        .annotate(count=Count("id"))
    )

    category_stock_raw = (
        Equipment.objects.values("category__name")
        .annotate(total=Sum("quantity_total"), available=Sum("quantity_available"))
        .order_by("category__name")
    )
    category_stock = [
        {
            "category": item["category__name"] or "Uncategorized",
            "total": item["total"] or 0,
            "available": item["available"] or 0,
        }
        for item in category_stock_raw
    ]

    context = {
        "equipment_total": equipment_total,
        "consumables_total": consumables_total,
        "low_stock_total": low_stock_total,
        "requests_pending": requests_pending,
        "active_checkouts": active_checkouts,
        "recent_requests": recent_requests,
        "recent_usage": recent_usage,
        "equipment_by_status": equipment_by_status,
        "requests_by_status": requests_by_status,
        "day_labels": day_labels,
        "requests_daily": series_from_queryset(request_daily_qs, "count"),
        "usage_daily": series_from_queryset(usage_daily_qs, "count"),
        "checkouts_daily": series_from_queryset(checkout_daily_qs, "count"),
        "category_stock": category_stock,
    }
    return render(request, "inventory/analytics.html", context)


@login_required
def equipment_list(request):
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    show_deleted = bool(request.session.get("show_deleted_global", False))
    manager = Equipment.all_objects if show_deleted else Equipment.objects
    queryset = manager.select_related("category", "workplace", "cabinet")

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    category = request.GET.get("category", "").strip()
    workplace = request.GET.get("workplace", "").strip()
    cabinet = request.GET.get("cabinet", "").strip()
    consumable = request.GET.get("consumable", "").strip()
    low_stock = request.GET.get("low_stock", "").strip()

    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(inventory_number__icontains=query)
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

    if cabinet:
        queryset = queryset.filter(cabinet_id=cabinet)

    if consumable:
        queryset = queryset.filter(is_consumable=consumable == "1")

    if low_stock:
        queryset = queryset.filter(quantity_available__lte=F("low_stock_threshold"))

    page_obj = _paginate(request, queryset, page_size)
    equipment_items = [_decorate_equipment(item) for item in page_obj.object_list]
    status_counts_raw = status_base_qs.values("status").annotate(count=Count("id"))
    status_counts = {item["status"]: item["count"] for item in status_counts_raw}

    base_filters = {
        "q": query,
        "category": category,
        "workplace": workplace,
        "cabinet": cabinet,
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
        "cabinets": Cabinet.objects.all(),
        "status_choices": [(value, status_label_map.get(value, value)) for value in VISIBLE_EQUIPMENT_STATUSES],
        "status_filter_links": status_filter_links,
        "filters": {
            "q": query,
            "status": status,
            "category": category,
            "workplace": workplace,
            "cabinet": cabinet,
            "consumable": consumable,
            "low_stock": low_stock,
            "show_deleted": "1" if show_deleted else "",
        },
        **_with_page_context(page_obj),
    }
    return render(request, "inventory/equipment_list.html", context)


@login_required
def usage_history(request):
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    show_deleted = bool(request.session.get("show_deleted_global", False))
    request_id = (request.GET.get("request_id") or "").strip()
    initial_request_id = int(request_id) if request_id.isdigit() else None
    can_create_usage = _can_create_usage(request.user)

    usage_form = MaterialUsageForm(initial_request_id=initial_request_id) if can_create_usage else None
    if request.method == "POST":
        if not can_create_usage:
            return forbidden(request, "Списание доступно только уполномоченным ролям.")
        usage_form = MaterialUsageForm(request.POST, initial_request_id=initial_request_id)
        if usage_form.is_valid():
            usage_obj = usage_form.save(commit=False)
            if usage_obj.related_request and usage_obj.related_request.processed_by_id:
                usage_obj.used_by = usage_obj.related_request.processed_by
            else:
                usage_obj.used_by = request.user
            usage_obj._actor = request.user
            usage_obj.save()
            messages.success(request, "Операция сохранена: выдача расходуемого или списание сломанного оборудования.")
            redirect_params = {}
            date_from_post = (request.POST.get("from") or "").strip()
            date_to_post = (request.POST.get("to") or "").strip()
            if date_from_post:
                redirect_params["from"] = date_from_post
            if date_to_post:
                redirect_params["to"] = date_to_post
            if redirect_params:
                return redirect(f"{reverse('usage_history')}?{urlencode(redirect_params)}")
            return redirect("usage_history")

    usage_manager = MaterialUsage.all_objects if show_deleted else MaterialUsage.objects
    usage = usage_manager.select_related("equipment", "equipment__cabinet", "used_by", "workplace").order_by("-used_at")
    if not _can_view_all_operational_data(request.user):
        usage = usage.filter(used_by=request.user)
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    if not date_from and "from" not in request.GET and preferences and preferences.default_usage_period_days:
        date_from = (timezone.localdate() - timedelta(days=preferences.default_usage_period_days)).isoformat()
    if date_from:
        usage = usage.filter(used_at__date__gte=date_from)
    if date_to:
        usage = usage.filter(used_at__date__lte=date_to)
    page_obj = _paginate(request, usage, page_size)
    return render(
        request,
        "inventory/usage_history.html",
        {
            "usage": page_obj.object_list,
            "filters": {"from": date_from, "to": date_to},
            "can_create_usage": can_create_usage,
            "usage_form": usage_form,
            "request_quantity_map": getattr(usage_form, "request_quantity_map", {}) if usage_form else {},
            "request_equipment_map": getattr(usage_form, "request_equipment_map", {}) if usage_form else {},
            "request_workplace_map": getattr(usage_form, "request_workplace_map", {}) if usage_form else {},
            "prefilled_request_id": initial_request_id,
            **_with_page_context(page_obj),
        },
    )


@login_required
def request_history(request):
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    show_deleted = bool(request.session.get("show_deleted_global", False))
    requests_manager = EquipmentRequest.all_objects if show_deleted else EquipmentRequest.objects
    requests = requests_manager.select_related("requester", "equipment", "workplace", "processed_by").order_by("-requested_at")
    view_mode = request.GET.get("view", "").strip()
    if not _can_view_all_operational_data(request.user):
        requests = requests.filter(requester=request.user)
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    if not status and "status" not in request.GET and preferences and preferences.default_request_status:
        status = preferences.default_request_status
    if not kind and "kind" not in request.GET and preferences and preferences.default_request_kind:
        kind = preferences.default_request_kind
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    if view_mode == "mine":
        requests = requests.filter(requester=request.user)
    elif view_mode == "processing":
        requests = requests.filter(status__in=[REQUEST_PENDING, REQUEST_APPROVED, REQUEST_ISSUED])
        if _can_process_request_status(request.user):
            requests = requests.exclude(processed_by=request.user, status__in=[REQUEST_APPROVED, REQUEST_ISSUED])

    if status:
        requests = requests.filter(status=status)
    if kind:
        requests = requests.filter(request_kind=kind)
    if date_from:
        requests = requests.filter(requested_at__date__gte=date_from)
    if date_to:
        requests = requests.filter(requested_at__date__lte=date_to)

    page_obj = _paginate(request, requests, page_size)
    request_items = [_decorate_request(item) for item in page_obj.object_list]
    can_quick_status = _can_process_request_status(request.user)

    return render(
        request,
        "inventory/request_history.html",
        {
            "requests": request_items,
            "status_choices": EquipmentRequest._meta.get_field("status").choices,
            "kind_choices": EquipmentRequest._meta.get_field("request_kind").choices,
            "filters": {"status": status, "kind": kind, "from": date_from, "to": date_to, "view": view_mode},
            "can_create_request": _can_create_request(request.user),
            "can_quick_status": can_quick_status,
            **_with_page_context(page_obj),
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
                "request_results": [],
                "usage_results": [],
                "checkout_results": [],
                "workplace_results": [],
                "cabinet_results": [],
            }
        )
        return render(request, "inventory/search.html", context)

    equipment_manager = Equipment.all_objects if show_deleted else Equipment.objects
    requests_manager = EquipmentRequest.all_objects if show_deleted else EquipmentRequest.objects
    usage_manager = MaterialUsage.all_objects if show_deleted else MaterialUsage.objects
    checkouts_manager = EquipmentCheckout.all_objects if show_deleted else EquipmentCheckout.objects
    workplaces_manager = Workplace.all_objects if show_deleted else Workplace.objects
    cabinets_manager = Cabinet.all_objects if show_deleted else Cabinet.objects

    equipment_results = (
        equipment_manager.select_related("category", "workplace")
        .filter(
            Q(name__icontains=q)
            | Q(inventory_number__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(model__icontains=q)
            | Q(category__name__icontains=q)
            | Q(workplace__name__icontains=q)
            | Q(cabinet__code__icontains=q)
        )
        .order_by("name")[:25]
    )
    if not _can_view_all_operational_data(request.user):
        requests_manager = requests_manager.filter(requester=request.user)
        usage_manager = usage_manager.filter(used_by=request.user)
        checkouts_manager = checkouts_manager.filter(taken_by=request.user)
    request_results = (
        requests_manager.select_related("requester", "equipment", "workplace")
        .filter(
            Q(requester__username__icontains=q)
            | Q(equipment__name__icontains=q)
            | Q(workplace__name__icontains=q)
            | Q(comment__icontains=q)
            | Q(status__icontains=q)
        )
        .order_by("-requested_at")[:25]
    )
    usage_results = (
        usage_manager.select_related("equipment", "used_by", "workplace")
        .filter(
            Q(equipment__name__icontains=q)
            | Q(used_by__username__icontains=q)
            | Q(workplace__name__icontains=q)
            | Q(note__icontains=q)
        )
        .order_by("-used_at")[:25]
    )
    checkout_results = (
        checkouts_manager.select_related("equipment", "taken_by", "workplace", "cabinet")
        .filter(
            Q(equipment__name__icontains=q)
            | Q(taken_by__username__icontains=q)
            | Q(workplace__name__icontains=q)
            | Q(cabinet__code__icontains=q)
            | Q(note__icontains=q)
        )
        .order_by("-taken_at")[:25]
    )
    workplace_results = workplaces_manager.filter(
        Q(name__icontains=q) | Q(location__icontains=q) | Q(description__icontains=q)
    ).order_by("name")[:25]
    cabinet_results = cabinets_manager.select_related("workplace").filter(
        Q(code__icontains=q) | Q(name__icontains=q) | Q(workplace__name__icontains=q) | Q(description__icontains=q)
    ).order_by("code")[:25]
    context.update(
        {
            "equipment_results": equipment_results,
            "request_results": request_results,
            "usage_results": usage_results,
            "checkout_results": checkout_results,
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
        {"workplaces": workplaces_qs, "members_by_workplace": members_by_workplace, "show_deleted": show_deleted},
    )


@login_required
def suppliers(request):
    return forbidden(request, "Раздел поставщиков отключён.")


@login_required
def cabinets(request):
    show_deleted = bool(request.session.get("show_deleted_global", False))
    cabinets_manager = Cabinet.all_objects if show_deleted else Cabinet.objects
    cabinets_qs = cabinets_manager.select_related("workplace").order_by("code")
    return render(request, "inventory/cabinets.html", {"cabinets": cabinets_qs, "show_deleted": show_deleted})


@login_required
def checkouts(request):
    return forbidden(request, "Раздел выдач отключён. Используйте раздел выдачи расходуемого/списания.")


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


@login_required
def reports(request):
    if not _can_access_reports(request.user):
        return forbidden(request, "Отчёты доступны только администратору и складу.")
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    cabinet_report = (
        Equipment.objects.filter(cabinet__isnull=False)
        .values("cabinet__code", "cabinet__name", "cabinet__workplace__name")
        .annotate(items=Count("id"), total=Sum("quantity_total"), available=Sum("quantity_available"))
        .order_by("cabinet__code")
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

    return render(
        request,
        "inventory/reports.html",
        {
            "cabinet_report": cabinet_report,
            "materials_report": materials_report,
            "filters": {"from": date_from, "to": date_to},
        },
    )


@login_required
def reports_export(request, report_type: str):
    if not _can_access_reports(request.user):
        return forbidden(request, "Экспорт отчётов доступен только администратору и складу.")
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{report_type}-report.csv"'
    writer = csv.writer(response)

    if report_type == "cabinets":
        writer.writerow(["Cabinet", "Workplace", "Items", "Total qty", "Available qty"])
        cabinet_report = (
            Equipment.objects.filter(cabinet__isnull=False)
            .values("cabinet__code", "cabinet__workplace__name")
            .annotate(items=Count("id"), total=Sum("quantity_total"), available=Sum("quantity_available"))
            .order_by("cabinet__code")
        )
        for item in cabinet_report:
            writer.writerow(
                [
                    item["cabinet__code"] or "-",
                    item["cabinet__workplace__name"] or "-",
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

    return HttpResponse("Unknown report type", status=400)


@login_required
def request_create(request):
    if not _can_create_request(request.user):
        return forbidden(request, "Заявки доступны только уполномоченным ролям.")

    if request.method == "POST":
        form = EquipmentRequestForm(request.POST)
        if form.is_valid():
            new_request = form.save(commit=False)
            new_request.requester = request.user
            new_request._actor = request.user
            new_request.save()
            messages.success(request, "Request saved.")
            return redirect("request_history")
    else:
        form = EquipmentRequestForm(initial={"needed_by": timezone.localdate() + timedelta(days=7)})

    return render(request, "inventory/request_form.html", {"form": form})


@login_required
def request_detail(request, request_id: int):
    item = get_object_or_404(
        EquipmentRequest.objects.select_related("requester", "equipment", "workplace", "processed_by"),
        pk=request_id,
    )
    can_access = _can_view_all_operational_data(request.user) or item.requester_id == request.user.pk
    if not can_access:
        return forbidden(request, "Просмотр этой заявки недоступен.")
    _decorate_request(item)

    message_form = EquipmentRequestMessageForm()
    photo_form = EquipmentRequestPhotoForm()
    if request.method == "POST":
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

    request_usage_url = reverse("usage_history")
    request_usage_url = f"{request_usage_url}?request_id={item.pk}"
    threaded_messages = _build_request_message_thread(
        item.messages.select_related("author", "parent").all()
    )
    return render(
        request,
        "inventory/request_detail.html",
        {
            "item": item,
            "messages_list": threaded_messages,
            "photos": item.photos.select_related("uploaded_by").all(),
            "message_form": message_form,
            "photo_form": photo_form,
            "request_usage_url": request_usage_url,
            "can_quick_status": _can_process_request_status(request.user),
            "status_choices": EquipmentRequest._meta.get_field("status").choices,
        },
    )


@login_required
@require_POST
def request_update_status(request, request_id: int):
    item = get_object_or_404(EquipmentRequest, pk=request_id)
    if not _can_process_request_status(request.user):
        return forbidden(request, "Быстрая смена статуса недоступна.")
    new_status = (request.POST.get("status") or "").strip()
    allowed_statuses = {value for value, _ in EquipmentRequest._meta.get_field("status").choices}
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
        item.save(update_fields=["status", "processed_by", "processed_at"])
        note_lines = [f"Статус изменён: {previous_status} -> {item.get_status_display()}."]
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
def usage_create(request):
    request_id = (request.GET.get("request_id") or "").strip()
    if request_id:
        return redirect(f"{reverse('usage_history')}?request_id={request_id}")
    return redirect("usage_history")


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
            messages.success(request, "Stock adjustment saved.")
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
    return forbidden(request, "Форма выдач отключена. Оформляйте только выдачу расходуемого в разделе списаний.")


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
            ]
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
def direct_messages_view(request):
    selected_user = None
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
    if selected_user is None and conversations:
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

    return render(
        request,
        "inventory/direct_messages.html",
        {
            "conversations": conversations,
            "selected_user": selected_user,
            "conversation_messages": conversation_messages,
            "form": form,
        },
    )


@login_required
def role_assignment(request):
    if not user_in_group(request.user, GROUP_ADMIN):
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
        return redirect("analytics")
    if request.method == "POST":
        form = RussianAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect("analytics")
    else:
        form = RussianAuthenticationForm(request)
    return render(request, "inventory/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("login")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("analytics")
    if request.method == "POST":
        form = RussianUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            return redirect("login")
    else:
        form = RussianUserCreationForm()
    return render(request, "inventory/register.html", {"form": form})


def password_reset_request_view(request):
    if request.user.is_authenticated:
        return redirect("analytics")

    if request.method == "POST":
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            user = User.objects.filter(is_active=True, email__iexact=email).first()
            if user:
                PasswordResetCode.objects.filter(user=user, email__iexact=email, used_at__isnull=True).update(
                    used_at=timezone.now()
                )
                code = _generate_password_reset_code()
                expires_at = timezone.now() + timedelta(minutes=PASSWORD_RESET_CODE_TTL_MINUTES)
                PasswordResetCode.objects.create(
                    user=user,
                    email=email,
                    code_hash=_password_reset_code_hash(email, code),
                    expires_at=expires_at,
                )
                send_mail(
                    "Код восстановления пароля",
                    (
                        f"Здравствуйте, {user.get_username()}!\n\n"
                        f"Код для восстановления пароля: {code}\n"
                        f"Код действует {PASSWORD_RESET_CODE_TTL_MINUTES} минут.\n\n"
                        "Если вы не запрашивали восстановление, просто проигнорируйте это письмо."
                    ),
                    settings.DEFAULT_FROM_EMAIL,
                    [email],
                    fail_silently=False,
                )

            messages.success(
                request,
                "Если адрес найден, мы отправили на него код для восстановления пароля.",
            )
            return redirect("password_reset_confirm")
    else:
        form = PasswordResetRequestForm()

    return render(request, "inventory/password_reset_request.html", {"form": form})


def password_reset_confirm_view(request):
    if request.user.is_authenticated:
        return redirect("analytics")

    if request.method == "POST":
        form = PasswordResetConfirmForm(request.POST)
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
                messages.success(request, "Пароль обновлён. Теперь можно войти.")
                return redirect("login")
    else:
        form = PasswordResetConfirmForm()

    return render(request, "inventory/password_reset_confirm.html", {"form": form})


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
        messages.success(request, f'Backup "{uploaded_file.name}" imported successfully.')
    except Exception as exc:
        messages.error(request, f"Backup import failed: {exc}")
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
        return HttpResponse("SQLite backup is available only for sqlite3 engine.", status=400)
    db_path = Path(str(db_cfg["NAME"]))
    if not db_path.exists():
        return HttpResponse("SQLite file not found.", status=404)
    return FileResponse(db_path.open("rb"), as_attachment=True, filename=db_path.name)


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
    search_url = request.build_absolute_uri(
        f"{reverse('equipment_list')}?q={item.inventory_number}"
    )
    img = qrcode.make(search_url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return HttpResponse(buf.getvalue(), content_type="image/png")
