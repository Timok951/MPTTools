import csv
from datetime import timedelta
import io
import os
from pathlib import Path
import tempfile

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.conf import settings
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
from core.models import Cabinet, EquipmentCategory, Supplier, UserPreference, Workplace, WorkplaceMember
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer, REQUEST_PENDING
from .authz import GROUP_ADMIN, GROUP_BUILDER, GROUP_SYSADMIN, GROUP_WAREHOUSE, user_in_group
from .forms import (
    BackupImportForm,
    EquipmentCheckoutForm,
    EquipmentRequestForm,
    InventoryAdjustmentForm,
    MaterialUsageForm,
    QuickTimerStartForm,
    RussianAuthenticationForm,
    RussianUserCreationForm,
    UserPreferenceForm,
    WorkTimerForm,
)

ROLE_DESCRIPTIONS = {
    GROUP_ADMIN: "Полный доступ, аналитика и контроль.",
    GROUP_WAREHOUSE: "Учёт склада, корректировки и выдача материалов.",
    GROUP_SYSADMIN: "Техническое обслуживание и заявки на оборудование.",
    GROUP_BUILDER: "Заявки и расход строительного оборудования.",
}


def _can_manage_timers(user) -> bool:
    return (
        user_in_group(user, GROUP_ADMIN)
        or user_in_group(user, GROUP_SYSADMIN)
        or user_in_group(user, GROUP_BUILDER)
    )


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
    inventory_due_total = sum(1 for item in Equipment.objects.all() if item.is_inventory_due)

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
        "inventory_due_total": inventory_due_total,
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
    queryset = manager.select_related("category", "supplier", "workplace", "cabinet")

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    category = request.GET.get("category", "").strip()
    workplace = request.GET.get("workplace", "").strip()
    supplier = request.GET.get("supplier", "").strip()
    cabinet = request.GET.get("cabinet", "").strip()
    consumable = request.GET.get("consumable", "").strip()
    low_stock = request.GET.get("low_stock", "").strip()
    inventory_due = request.GET.get("inventory_due", "").strip()

    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(inventory_number__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(model__icontains=query)
        )

    if status:
        queryset = queryset.filter(status=status)

    if category:
        queryset = queryset.filter(category_id=category)

    if workplace:
        queryset = queryset.filter(workplace_id=workplace)

    if supplier:
        queryset = queryset.filter(supplier_id=supplier)

    if cabinet:
        queryset = queryset.filter(cabinet_id=cabinet)

    if consumable:
        queryset = queryset.filter(is_consumable=consumable == "1")

    if low_stock:
        queryset = queryset.filter(quantity_available__lte=F("low_stock_threshold"))

    if inventory_due:
        queryset = [item for item in queryset if item.is_inventory_due]

    page_obj = _paginate(request, queryset, page_size)

    context = {
        "equipment": page_obj.object_list,
        "categories": EquipmentCategory.objects.all(),
        "workplaces": Workplace.objects.all(),
        "suppliers": Supplier.objects.all(),
        "cabinets": Cabinet.objects.all(),
        "status_choices": Equipment._meta.get_field("status").choices,
        "filters": {
            "q": query,
            "status": status,
            "category": category,
            "workplace": workplace,
            "supplier": supplier,
            "cabinet": cabinet,
            "consumable": consumable,
            "low_stock": low_stock,
            "inventory_due": inventory_due,
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
    usage_manager = MaterialUsage.all_objects if show_deleted else MaterialUsage.objects
    usage = usage_manager.select_related("equipment", "used_by", "workplace").order_by("-used_at")
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
            **_with_page_context(page_obj),
        },
    )


@login_required
def request_history(request):
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    show_deleted = bool(request.session.get("show_deleted_global", False))
    requests_manager = EquipmentRequest.all_objects if show_deleted else EquipmentRequest.objects
    requests = requests_manager.select_related("requester", "equipment", "workplace").order_by("-requested_at")
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    if not status and "status" not in request.GET and preferences and preferences.default_request_status:
        status = preferences.default_request_status
    if not kind and "kind" not in request.GET and preferences and preferences.default_request_kind:
        kind = preferences.default_request_kind
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    if status:
        requests = requests.filter(status=status)
    if kind:
        requests = requests.filter(request_kind=kind)
    if date_from:
        requests = requests.filter(requested_at__date__gte=date_from)
    if date_to:
        requests = requests.filter(requested_at__date__lte=date_to)

    page_obj = _paginate(request, requests, page_size)

    return render(
        request,
        "inventory/request_history.html",
        {
            "requests": page_obj.object_list,
            "status_choices": EquipmentRequest._meta.get_field("status").choices,
            "kind_choices": EquipmentRequest._meta.get_field("request_kind").choices,
            "filters": {"status": status, "kind": kind, "from": date_from, "to": date_to},
            **_with_page_context(page_obj),
        },
    )


@login_required
def timer_panel(request):
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    show_deleted = bool(request.session.get("show_deleted_global", False))
    timers_manager = WorkTimer.all_objects if show_deleted else WorkTimer.objects
    timers = timers_manager.select_related("user", "workplace", "equipment").order_by("-started_at")
    timer_status = request.GET.get("status", "").strip()
    using_default_status_filter = False
    if not timer_status and "status" not in request.GET and preferences and preferences.default_timer_status:
        timer_status = preferences.default_timer_status
        using_default_status_filter = True
    if timer_status == "active":
        timers = timers.filter(ended_at__isnull=True)
    elif timer_status == "finished":
        timers = timers.filter(ended_at__isnull=False)

    active_timers_total = timers_manager.filter(ended_at__isnull=True).count()
    page_obj = _paginate(request, timers, page_size)
    now = timezone.now()
    for timer in page_obj.object_list:
        if timer.ended_at:
            timer.duration_seconds_live = max(int((timer.ended_at - timer.started_at).total_seconds()), 0)
            timer.is_active = False
        else:
            timer.duration_seconds_live = max(int((now - timer.started_at).total_seconds()), 0)
            timer.is_active = True

    context = {
        "timers": page_obj.object_list,
        "filters": {"status": timer_status},
        "active_timers_total": active_timers_total,
        "can_manage_timers": _can_manage_timers(request.user),
        "quick_timer_form": QuickTimerStartForm(),
        "using_default_status_filter": using_default_status_filter,
        **_with_page_context(page_obj),
    }
    return render(request, "inventory/timer_panel.html", context)


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
                "supplier_results": [],
            }
        )
        return render(request, "inventory/search.html", context)

    equipment_manager = Equipment.all_objects if show_deleted else Equipment.objects
    requests_manager = EquipmentRequest.all_objects if show_deleted else EquipmentRequest.objects
    usage_manager = MaterialUsage.all_objects if show_deleted else MaterialUsage.objects
    checkouts_manager = EquipmentCheckout.all_objects if show_deleted else EquipmentCheckout.objects
    workplaces_manager = Workplace.all_objects if show_deleted else Workplace.objects
    cabinets_manager = Cabinet.all_objects if show_deleted else Cabinet.objects
    suppliers_manager = Supplier.all_objects if show_deleted else Supplier.objects

    equipment_results = (
        equipment_manager.select_related("category", "workplace", "supplier")
        .filter(
            Q(name__icontains=q)
            | Q(inventory_number__icontains=q)
            | Q(serial_number__icontains=q)
            | Q(model__icontains=q)
            | Q(category__name__icontains=q)
            | Q(workplace__name__icontains=q)
            | Q(cabinet__code__icontains=q)
            | Q(supplier__name__icontains=q)
        )
        .order_by("name")[:25]
    )
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
    supplier_results = suppliers_manager.filter(
        Q(name__icontains=q)
        | Q(contact_name__icontains=q)
        | Q(phone__icontains=q)
        | Q(email__icontains=q)
        | Q(address__icontains=q)
    ).order_by("name")[:25]

    context.update(
        {
            "equipment_results": equipment_results,
            "request_results": request_results,
            "usage_results": usage_results,
            "checkout_results": checkout_results,
            "workplace_results": workplace_results,
            "cabinet_results": cabinet_results,
            "supplier_results": supplier_results,
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
    show_deleted = bool(request.session.get("show_deleted_global", False))
    suppliers_manager = Supplier.all_objects if show_deleted else Supplier.objects
    suppliers_qs = suppliers_manager.all().order_by("name")
    return render(request, "inventory/suppliers.html", {"suppliers": suppliers_qs, "show_deleted": show_deleted})


@login_required
def cabinets(request):
    show_deleted = bool(request.session.get("show_deleted_global", False))
    cabinets_manager = Cabinet.all_objects if show_deleted else Cabinet.objects
    cabinets_qs = cabinets_manager.select_related("workplace").order_by("code")
    return render(request, "inventory/cabinets.html", {"cabinets": cabinets_qs, "show_deleted": show_deleted})


@login_required
def checkouts(request):
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    show_deleted = bool(request.session.get("show_deleted_global", False))
    checkout_manager = EquipmentCheckout.all_objects if show_deleted else EquipmentCheckout.objects
    checkout_qs = checkout_manager.select_related("equipment", "taken_by", "workplace", "cabinet", "related_request")
    status = request.GET.get("status", "").strip()
    if not status and "status" not in request.GET and preferences and preferences.default_checkout_status:
        status = preferences.default_checkout_status
    if status == "active":
        checkout_qs = checkout_qs.filter(returned_at__isnull=True)
    elif status == "returned":
        checkout_qs = checkout_qs.filter(returned_at__isnull=False)
    page_obj = _paginate(request, checkout_qs, page_size)
    return render(
        request,
        "inventory/checkouts.html",
        {
            "checkouts": page_obj.object_list,
            "filters": {"status": status},
            **_with_page_context(page_obj),
        },
    )


@login_required
def history_timeline(request):
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
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    response = HttpResponse(content_type="application/vnd.ms-excel")
    response["Content-Disposition"] = f"attachment; filename={report_type}-report.xls"
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
    if not (
        user_in_group(request.user, GROUP_ADMIN)
        or user_in_group(request.user, GROUP_SYSADMIN)
        or user_in_group(request.user, GROUP_BUILDER)
    ):
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
def usage_create(request):
    if not (
        user_in_group(request.user, GROUP_ADMIN)
        or user_in_group(request.user, GROUP_WAREHOUSE)
        or user_in_group(request.user, GROUP_SYSADMIN)
        or user_in_group(request.user, GROUP_BUILDER)
    ):
        return forbidden(request, "Списание доступно только уполномоченным ролям.")

    if request.method == "POST":
        form = MaterialUsageForm(request.POST)
        if form.is_valid():
            usage = form.save(commit=False)
            usage.used_by = request.user
            usage._actor = request.user
            usage.save()
            messages.success(request, "Usage record saved.")
            return redirect("usage_history")
    else:
        form = MaterialUsageForm()

    return render(request, "inventory/usage_form.html", {"form": form})


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
    if not (
        user_in_group(request.user, GROUP_ADMIN)
        or user_in_group(request.user, GROUP_SYSADMIN)
        or user_in_group(request.user, GROUP_BUILDER)
    ):
        return forbidden(request, "Таймер доступен только уполномоченным ролям.")

    if request.method == "POST":
        form = WorkTimerForm(request.POST)
        if form.is_valid():
            timer = form.save(commit=False)
            timer.user = request.user
            timer._actor = request.user
            timer.save()
            messages.success(request, "Timer entry saved.")
            return redirect("timer_panel")
    else:
        form = WorkTimerForm()

    return render(request, "inventory/timer_form.html", {"form": form, "quick_form": QuickTimerStartForm()})


@login_required
def timer_quick_start(request):
    if not (
        user_in_group(request.user, GROUP_ADMIN)
        or user_in_group(request.user, GROUP_SYSADMIN)
        or user_in_group(request.user, GROUP_BUILDER)
    ):
        return forbidden(request, "Таймер доступен только уполномоченным ролям.")
    if request.method != "POST":
        return redirect("timer_panel")

    form = QuickTimerStartForm(request.POST)
    if form.is_valid():
        timer = WorkTimer(
            user=request.user,
            workplace=form.cleaned_data["workplace"],
            equipment=form.cleaned_data["equipment"],
            note=form.cleaned_data["note"],
            started_at=timezone.now(),
        )
        timer._actor = request.user
        timer.save()
        messages.success(request, "Timer started.")
    else:
        messages.error(request, "Quick timer could not be started. Check the selected values.")
    return redirect("timer_panel")


@login_required
def timer_stop(request, timer_id: int):
    timer = get_object_or_404(WorkTimer, pk=timer_id)
    if not _can_manage_timers(request.user):
        return forbidden(request, "Таймер доступен только уполномоченным ролям.")
    if timer.ended_at:
        return redirect("timer_panel")
    timer.ended_at = timezone.now()
    timer._actor = request.user
    timer.save(update_fields=["ended_at"])
    messages.success(request, "Timer stopped.")
    return redirect("timer_panel")


@login_required
def checkout_create(request):
    if not (
        user_in_group(request.user, GROUP_ADMIN)
        or user_in_group(request.user, GROUP_SYSADMIN)
        or user_in_group(request.user, GROUP_BUILDER)
    ):
        return forbidden(request, "Выдача доступна только уполномоченным ролям.")

    if request.method == "POST":
        form = EquipmentCheckoutForm(request.POST, user=request.user)
        if form.is_valid():
            checkout = form.save(commit=False)
            checkout.taken_by = request.user
            checkout._actor = request.user
            checkout.save()
            messages.success(request, "Checkout saved.")
            return redirect("checkouts")
    else:
        form = EquipmentCheckoutForm(
            initial={
                "taken_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "due_at": (timezone.localtime() + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M"),
            },
            user=request.user,
        )

    return render(request, "inventory/checkout_form.html", {"form": form})


@login_required
def checkout_return(request, checkout_id: int):
    checkout = get_object_or_404(EquipmentCheckout, pk=checkout_id)
    if checkout.returned_at:
        return redirect("checkouts")
    checkout.returned_at = timezone.now()
    checkout._actor = request.user
    checkout.save(update_fields=["returned_at"])
    messages.success(request, "Checkout marked as returned.")
    return redirect("checkouts")


@login_required
def user_preferences_view(request):
    preferences = _get_user_preferences(request.user)
    if request.method == "POST":
        form = UserPreferenceForm(request.POST, instance=preferences)
        if form.is_valid():
            saved = form.save()
            if saved.preferred_language:
                translation.activate(saved.preferred_language)
                request.session["django_language"] = saved.preferred_language
            messages.success(request, "Preferences updated.")
            return redirect("user_preferences")
    else:
        form = UserPreferenceForm(instance=preferences)

    return render(request, "inventory/user_preferences.html", {"form": form})


@login_required
def api_docs(request):
    return render(
        request,
        "inventory/api_docs.html",
        {
            "endpoints": [
                "/api/v1/equipment/",
                "/api/v1/workplaces/",
                "/api/v1/cabinets/",
                "/api/v1/categories/",
                "/api/v1/suppliers/",
                "/api/v1/requests/",
                "/api/v1/usage/",
                "/api/v1/checkouts/",
                "/api/v1/timers/",
            ]
        },
    )


@login_required
def role_assignment(request):
    if not user_in_group(request.user, GROUP_ADMIN):
        return forbidden(request, "Выдача ролей доступна только администратору.")

    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLE_DESCRIPTIONS}

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        role_name = request.POST.get("role")
        target_user = get_object_or_404(User, pk=user_id)
        if role_name in groups:
            target_user.groups.clear()
            target_user.groups.add(groups[role_name])
        return redirect("role_assignment")

    users = User.objects.all().order_by("username")
    return render(
        request,
        "inventory/role_assignment.html",
        {"users": users, "roles": ROLE_DESCRIPTIONS},
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


@login_required
def toggle_show_deleted(request):
    if request.method == "POST":
        request.session["show_deleted_global"] = request.POST.get("show_deleted_global") == "1"
        request.session.modified = True
    back = request.META.get("HTTP_REFERER") or reverse("analytics")
    return redirect(back)


def _data_tools_context():
    sqlite_engine = settings.DATABASES["default"]["ENGINE"].endswith("sqlite3")
    return {
        "sqlite_available": sqlite_engine,
        "backup_import_form": BackupImportForm(),
    }


@login_required
def data_tools(request):
    if not user_in_group(request.user, GROUP_ADMIN):
        return forbidden(request, "Инструменты данных доступны только администратору.")
    return render(request, "inventory/tools/data_io.html", _data_tools_context())


@login_required
def import_json_backup(request):
    if not user_in_group(request.user, GROUP_ADMIN):
        return forbidden(request, "Импорт доступен только администратору.")
    if request.method != "POST":
        return redirect("data_tools")

    form = BackupImportForm(request.POST, request.FILES)
    if not form.is_valid():
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        return render(request, "inventory/tools/data_io.html", {**_data_tools_context(), "backup_import_form": form}, status=400)

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
    if not user_in_group(request.user, GROUP_ADMIN):
        return forbidden(request, "Экспорт доступен только администратору.")
    out = io.StringIO()
    call_command("dumpdata", indent=2, stdout=out)
    response = HttpResponse(out.getvalue(), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="backup.json"'
    return response


@login_required
def download_sqlite_backup(request):
    if not user_in_group(request.user, GROUP_ADMIN):
        return forbidden(request, "Экспорт доступен только администратору.")
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
