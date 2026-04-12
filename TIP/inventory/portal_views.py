from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, User
from django.db import models
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import Cabinet, EquipmentCategory, Supplier, Workplace, WorkplaceMember
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer
from audit.models import AdminPortalLog
from audit.portal_log import log_portal_action

from .admin_procedures import (
    finish_abandoned_timers,
    reject_stale_requests,
    restock_low_stock_consumables,
)
from .authz import is_portal_admin
from .portal_forms import (
    FinishAbandonedTimersProcedureForm,
    PortalCabinetForm,
    PortalEquipmentCategoryForm,
    PortalEquipmentCheckoutForm,
    PortalEquipmentForm,
    PortalEquipmentRequestForm,
    PortalGroupForm,
    PortalInventoryAdjustmentForm,
    PortalMaterialUsageForm,
    PortalSupplierForm,
    PortalUserForm,
    PortalWorkplaceForm,
    PortalWorkplaceMemberForm,
    PortalWorkTimerForm,
    RejectStaleRequestsProcedureForm,
)
from .views import forbidden


@dataclass(frozen=True)
class PortalEntity:
    slug: str
    model: type[models.Model]
    form_class: type
    list_fields: tuple[str, ...]
    title: Any


PORTAL_ENTITIES: tuple[PortalEntity, ...] = (
    PortalEntity("equipment", Equipment, PortalEquipmentForm, ("name", "inventory_number", "status", "quantity_available", "deleted_at"), "Оборудование"),
    PortalEntity("categories", EquipmentCategory, PortalEquipmentCategoryForm, ("name", "deleted_at"), "Категории"),
    PortalEntity("suppliers", Supplier, PortalSupplierForm, ("name", "phone", "deleted_at"), "Поставщики"),
    PortalEntity("workplaces", Workplace, PortalWorkplaceForm, ("name", "location", "deleted_at"), "Рабочие места"),
    PortalEntity("cabinets", Cabinet, PortalCabinetForm, ("code", "name", "workplace", "deleted_at"), "Шкафы"),
    PortalEntity("workplace-members", WorkplaceMember, PortalWorkplaceMemberForm, ("workplace", "user", "role", "deleted_at"), "Сотрудники"),
    PortalEntity("adjustments", InventoryAdjustment, PortalInventoryAdjustmentForm, ("equipment", "delta", "reason", "created_at", "deleted_at"), "Корректировки"),
    PortalEntity("checkouts", EquipmentCheckout, PortalEquipmentCheckoutForm, ("equipment", "quantity", "taken_by", "returned_at", "deleted_at"), "Выдачи"),
    PortalEntity("requests", EquipmentRequest, PortalEquipmentRequestForm, ("requester", "equipment", "quantity", "status", "deleted_at"), "Заявки"),
    PortalEntity("usage", MaterialUsage, PortalMaterialUsageForm, ("equipment", "quantity", "used_by", "used_at", "deleted_at"), "Списания"),
    PortalEntity("timers", WorkTimer, PortalWorkTimerForm, ("user", "equipment", "started_at", "ended_at", "deleted_at"), "Таймеры"),
    PortalEntity("users", User, PortalUserForm, ("username", "email", "is_active", "is_staff", "is_superuser"), "Пользователи"),
    PortalEntity("groups", Group, PortalGroupForm, ("name",), "Группы и роли"),
)
PORTAL_BY_SLUG = {e.slug: e for e in PORTAL_ENTITIES}


def _portal_nav_context(current_slug: str | None = None):
    return {"entities": PORTAL_ENTITIES, "current_entity_slug": current_slug}


def _procedure_cards():
    return [
        {
            "slug": "reject_stale_requests",
            "title": _("Отклонить старые заявки"),
            "description": _("Помечает старые необработанные заявки как отклонённые и фиксирует, кто их обработал."),
            "form": RejectStaleRequestsProcedureForm(prefix="reject"),
        },
        {
            "slug": "finish_abandoned_timers",
            "title": _("Завершить брошенные таймеры"),
            "description": _("Закрывает давно запущенные таймеры, которые остались незавершёнными."),
            "form": FinishAbandonedTimersProcedureForm(prefix="timers"),
        },
        {
            "slug": "restock_low_stock_consumables",
            "title": _("Пополнить расходники с низким остатком"),
            "description": _("Создаёт корректировки остатков для расходников, которые опустились ниже порога."),
            "form": None,
        },
    ]


def _manager(model):
    return getattr(model, "all_objects", model.objects)


def _portal_guard(request):
    if not is_portal_admin(request.user):
        return forbidden(request, "Портал доступен только администраторам.")
    return None


def _get_entity_or_404(slug: str) -> PortalEntity:
    if slug not in PORTAL_BY_SLUG:
        from django.http import Http404

        raise Http404("Unknown entity")
    return PORTAL_BY_SLUG[slug]


def _list_headers(model: type[models.Model], fields: tuple[str, ...]):
    headers = []
    for name in fields:
        try:
            headers.append(model._meta.get_field(name).verbose_name)
        except Exception:
            headers.append(name.replace("_", " ").title())
    return headers


def _friendly_integrity_message(exc: Exception) -> str:
    text = str(exc)
    if "inventory_number" in text:
        return _("Такой инвентарный номер уже существует. Укажите другой.")
    return _("Не удалось сохранить запись из-за дублирующегося или некорректного уникального значения.")


@login_required
def portal_dashboard(request):
    if resp := _portal_guard(request):
        return resp
    return render(
        request,
        "inventory/portal/dashboard.html",
        {
            **_portal_nav_context("__home"),
            "procedure_cards": _procedure_cards(),
        },
    )


@login_required
def portal_logs(request):
    if resp := _portal_guard(request):
        return resp
    logs = AdminPortalLog.objects.select_related("actor").all()[:500]
    return render(
        request,
        "inventory/portal/logs.html",
        {
            **_portal_nav_context("__logs"),
            "logs": logs,
        },
    )


@login_required
def portal_list(request, entity: str):
    if resp := _portal_guard(request):
        return resp
    cfg = _get_entity_or_404(entity)
    show_deleted = bool(request.session.get("show_deleted_global", False))
    has_soft_delete = any(f.name == "deleted_at" for f in cfg.model._meta.fields)
    qs = _manager(cfg.model).all()
    if has_soft_delete and not show_deleted:
        qs = qs.filter(deleted_at__isnull=True)
    ordering = getattr(cfg.model._meta, "ordering", None) or ("-pk",)
    qs = qs.order_by(*ordering)
    return render(
        request,
        "inventory/portal/object_list.html",
        {
            **_portal_nav_context(cfg.slug),
            "cfg": cfg,
            "objects": qs,
            "show_deleted": show_deleted,
            "has_soft_delete": has_soft_delete,
            "list_headers": _list_headers(cfg.model, cfg.list_fields),
        },
    )


@login_required
def portal_create(request, entity: str):
    if resp := _portal_guard(request):
        return resp
    cfg = _get_entity_or_404(entity)
    Form = cfg.form_class
    if request.method == "POST":
        form = Form(request.POST, request.FILES)
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                if hasattr(obj, "_actor"):
                    obj._actor = request.user
                obj.save()
                if hasattr(form, "save_m2m"):
                    form.save_m2m()
                log_portal_action(request, "create", cfg.slug, obj=obj, meta={"pk": obj.pk})
                return redirect("portal_list", entity=cfg.slug)
            except IntegrityError as exc:
                form.add_error(None, _friendly_integrity_message(exc))
    else:
        form = Form()
    return render(request, "inventory/portal/object_form.html", {**_portal_nav_context(cfg.slug), "cfg": cfg, "form": form, "is_edit": False})


@login_required
def portal_edit(request, entity: str, pk: int):
    if resp := _portal_guard(request):
        return resp
    cfg = _get_entity_or_404(entity)
    obj = get_object_or_404(_manager(cfg.model), pk=pk)
    Form = cfg.form_class
    if request.method == "POST":
        form = Form(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            try:
                saved = form.save(commit=False)
                if hasattr(saved, "_actor"):
                    saved._actor = request.user
                saved.save()
                if hasattr(form, "save_m2m"):
                    form.save_m2m()
                log_portal_action(request, "update", cfg.slug, obj=saved, meta={"pk": saved.pk})
                return redirect("portal_list", entity=cfg.slug)
            except IntegrityError as exc:
                form.add_error(None, _friendly_integrity_message(exc))
    else:
        form = Form(instance=obj)
    return render(request, "inventory/portal/object_form.html", {**_portal_nav_context(cfg.slug), "cfg": cfg, "form": form, "is_edit": True, "object": obj})


@login_required
def portal_delete(request, entity: str, pk: int):
    if resp := _portal_guard(request):
        return resp
    cfg = _get_entity_or_404(entity)
    obj = get_object_or_404(_manager(cfg.model), pk=pk)
    if cfg.model is User and obj.pk == request.user.pk:
        messages.error(request, "Нельзя удалить собственную учётную запись.")
        return redirect("portal_list", entity=cfg.slug)
    if request.method == "POST":
        if hasattr(obj, "_actor"):
            obj._actor = request.user
        obj_repr = str(obj)
        obj_pk = obj.pk
        try:
            obj.delete()
            log_portal_action(request, "delete", cfg.slug, obj=obj_repr, meta={"pk": obj_pk})
            return redirect("portal_list", entity=cfg.slug)
        except ProtectedError:
            messages.error(request, _("Эту запись нельзя удалить, потому что она используется связанными данными."))
            return redirect("portal_list", entity=cfg.slug)
    return render(request, "inventory/portal/object_confirm_delete.html", {**_portal_nav_context(cfg.slug), "cfg": cfg, "object": obj})


@login_required
def portal_restore(request, entity: str, pk: int):
    if resp := _portal_guard(request):
        return resp
    cfg = _get_entity_or_404(entity)
    if not hasattr(cfg.model, "restore"):
        from django.http import Http404

        raise Http404()
    obj = get_object_or_404(cfg.model.all_objects, pk=pk)
    if request.method == "POST":
        obj.restore()
        log_portal_action(request, "restore", cfg.slug, obj=obj, meta={"pk": obj.pk})
        return redirect("portal_list", entity=cfg.slug)
    return render(request, "inventory/portal/object_confirm_restore.html", {**_portal_nav_context(cfg.slug), "cfg": cfg, "object": obj})


@login_required
def portal_procedure_run(request, slug: str):
    if resp := _portal_guard(request):
        return resp
    if request.method != "POST":
        return redirect("portal_home")

    if slug == "reject_stale_requests":
        form = RejectStaleRequestsProcedureForm(request.POST, prefix="reject")
        if not form.is_valid():
            messages.error(request, _("Укажите корректный срок давности для заявок."))
            return redirect("portal_home")
        result = reject_stale_requests(actor=request.user, stale_days=form.cleaned_data["stale_days"])
    elif slug == "finish_abandoned_timers":
        form = FinishAbandonedTimersProcedureForm(request.POST, prefix="timers")
        if not form.is_valid():
            messages.error(request, _("Укажите корректный срок давности для таймеров."))
            return redirect("portal_home")
        result = finish_abandoned_timers(actor=request.user, stale_hours=form.cleaned_data["stale_hours"])
    elif slug == "restock_low_stock_consumables":
        result = restock_low_stock_consumables(actor=request.user)
    else:
        messages.error(request, _("Неизвестная процедура."))
        return redirect("portal_home")

    log_portal_action(
        request,
        "procedure",
        slug,
        obj=result.title,
        meta={
            "processed_count": result.processed_count,
            "detail": result.detail,
            "execution_mode": result.execution_mode,
        },
    )
    messages.success(request, result.detail)
    return redirect("portal_home")
