import csv
from datetime import timedelta

from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import Group, User
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from assets.models import Equipment, EquipmentCheckout
from audit.models import AuditLog
from core.models import Cabinet, EquipmentCategory, Supplier, Workplace, WorkplaceMember
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer, REQUEST_PENDING
from .forms import (
    EquipmentCheckoutForm,
    EquipmentRequestForm,
    InventoryAdjustmentForm,
    MaterialUsageForm,
    WorkTimerForm,
)

GROUP_ADMIN = "Administrator"
GROUP_WAREHOUSE = "Warehouse"
GROUP_SYSADMIN = "Sysadmin"
GROUP_BUILDER = "Builder"

ROLE_DESCRIPTIONS = {
    GROUP_ADMIN: "Полный доступ, аналитика и контроль.",
    GROUP_WAREHOUSE: "Учёт склада, корректировки и выдача материалов.",
    GROUP_SYSADMIN: "Техническое обслуживание и заявки на оборудование.",
    GROUP_BUILDER: "Заявки и расход строительного оборудования.",
}


def user_in_group(user, group_name: str) -> bool:
    return user.is_superuser or user.groups.filter(name=group_name).exists()


def forbidden(request, message: str):
    back_url = request.META.get("HTTP_REFERER") or reverse("analytics")
    return render(
        request,
        "inventory/forbidden.html",
        {"message": message, "back_url": back_url},
        status=403,
    )


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
    queryset = Equipment.objects.select_related("category", "supplier", "workplace", "cabinet")

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

    context = {
        "equipment": queryset,
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
        },
    }
    return render(request, "inventory/equipment_list.html", context)


@login_required
def usage_history(request):
    usage = MaterialUsage.objects.select_related("equipment", "used_by", "workplace").order_by("-used_at")
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()
    if date_from:
        usage = usage.filter(used_at__date__gte=date_from)
    if date_to:
        usage = usage.filter(used_at__date__lte=date_to)
    return render(request, "inventory/usage_history.html", {"usage": usage, "filters": {"from": date_from, "to": date_to}})


@login_required
def request_history(request):
    requests = EquipmentRequest.objects.select_related("requester", "equipment", "workplace").order_by("-requested_at")
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
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

    return render(
        request,
        "inventory/request_history.html",
        {
            "requests": requests,
            "status_choices": EquipmentRequest._meta.get_field("status").choices,
            "kind_choices": EquipmentRequest._meta.get_field("request_kind").choices,
            "filters": {"status": status, "kind": kind, "from": date_from, "to": date_to},
        },
    )


@login_required
def timer_panel(request):
    timers = WorkTimer.objects.select_related("user", "workplace", "equipment").order_by("-started_at")
    return render(request, "inventory/timer_panel.html", {"timers": timers})


@login_required
def inventory_search(request):
    return equipment_list(request)


@login_required
def workplaces(request):
    workplaces_qs = Workplace.objects.all().order_by("name")
    members = WorkplaceMember.objects.select_related("user", "workplace")
    members_by_workplace = {}
    for member in members:
        members_by_workplace.setdefault(member.workplace_id, []).append(member)

    return render(
        request,
        "inventory/workplaces.html",
        {"workplaces": workplaces_qs, "members_by_workplace": members_by_workplace},
    )


@login_required
def suppliers(request):
    suppliers_qs = Supplier.objects.all().order_by("name")
    return render(request, "inventory/suppliers.html", {"suppliers": suppliers_qs})


@login_required
def cabinets(request):
    cabinets_qs = Cabinet.objects.select_related("workplace").order_by("code")
    return render(request, "inventory/cabinets.html", {"cabinets": cabinets_qs})


@login_required
def checkouts(request):
    checkout_qs = EquipmentCheckout.objects.select_related("equipment", "taken_by", "workplace", "cabinet", "related_request")
    status = request.GET.get("status", "").strip()
    if status == "active":
        checkout_qs = checkout_qs.filter(returned_at__isnull=True)
    elif status == "returned":
        checkout_qs = checkout_qs.filter(returned_at__isnull=False)
    return render(request, "inventory/checkouts.html", {"checkouts": checkout_qs, "filters": {"status": status}})


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
            return redirect("request_history")
    else:
        form = EquipmentRequestForm()

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
            return redirect("equipment_list")
    else:
        form = InventoryAdjustmentForm()

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
            return redirect("timer_panel")
    else:
        form = WorkTimerForm()

    return render(request, "inventory/timer_form.html", {"form": form})


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
            return redirect("checkouts")
    else:
        form = EquipmentCheckoutForm(initial={"taken_at": timezone.now()}, user=request.user)

    return render(request, "inventory/checkout_form.html", {"form": form})


@login_required
def checkout_return(request, checkout_id: int):
    checkout = get_object_or_404(EquipmentCheckout, pk=checkout_id)
    if checkout.returned_at:
        return redirect("checkouts")
    checkout.returned_at = timezone.now()
    checkout._actor = request.user
    checkout.save(update_fields=["returned_at"])
    return redirect("checkouts")


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
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect("analytics")
    else:
        form = AuthenticationForm(request)
    return render(request, "inventory/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("login")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("analytics")
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            return redirect("login")
    else:
        form = UserCreationForm()
    return render(request, "inventory/register.html", {"form": form})
