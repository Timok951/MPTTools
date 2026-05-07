from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group, User
from django.conf import settings
from django.db import models
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from assets.models import Equipment
from core.models import Cabinet, EmployeeSchedule, EquipmentCategory, RegistrationAllowedEmailDomain, Workplace
from operations.models import EquipmentRequest, PeriodicMaterialUsageSchedule
from audit.models import AdminPortalLog
from audit.portal_log import log_portal_action

from .admin_procedures import (
    reject_stale_requests,
    restock_low_stock_consumables,
)
from .authz import user_has_capability
from .portal_forms import (
    PortalCabinetForm,
    PortalEquipmentCategoryForm,
    PortalEquipmentForm,
    PortalEquipmentRequestForm,
    PortalGroupForm,
    PortalPeriodicMaterialUsageScheduleForm,
    PortalEmployeeScheduleForm,
    PortalRegistrationAllowedEmailDomainForm,
    PortalUserForm,
    PortalWorkplaceForm,
    RejectStaleRequestsProcedureForm,
    RestockLowStockConsumablesProcedureForm,
)
from .views import _get_user_preferences, _paginate, _with_page_context, forbidden


@dataclass(frozen=True)
class PortalEntity:
    slug: str
    model: type[models.Model]
    form_class: type
    list_fields: tuple[str, ...]
    title: Any


PORTAL_ENTITIES: tuple[PortalEntity, ...] = (
    PortalEntity("equipment", Equipment, PortalEquipmentForm, ("name", "serial_number", "status", "quantity_total", "deleted_at"), "Оборудование"),
    PortalEntity("categories", EquipmentCategory, PortalEquipmentCategoryForm, ("name", "deleted_at"), "Категории"),
    PortalEntity("workplaces", Workplace, PortalWorkplaceForm, ("name", "location", "deleted_at"), "Рабочие места"),
    PortalEntity("cabinets", Cabinet, PortalCabinetForm, ("name", "workplace", "deleted_at"), "Кабинеты"),
    PortalEntity(
        "requests",
        EquipmentRequest,
        PortalEquipmentRequestForm,
        ("requester", "equipment", "cabinet", "quantity", "status", "deleted_at"),
        "Заявки",
    ),
    PortalEntity(
        "periodic-usage",
        PeriodicMaterialUsageSchedule,
        PortalPeriodicMaterialUsageScheduleForm,
        ("title", "equipment", "quantity", "next_run_on", "is_active", "deleted_at"),
        "Периодический расход (расписание)",
    ),
    PortalEntity("users", User, PortalUserForm, ("username", "email", "is_active", "is_staff", "is_superuser"), "Пользователи"),
    PortalEntity("groups", Group, PortalGroupForm, ("name",), "Группы и роли"),
    PortalEntity(
        "registration-domains",
        RegistrationAllowedEmailDomain,
        PortalRegistrationAllowedEmailDomainForm,
        ("domain", "is_active", "notes"),
        "Домены почты для регистрации",
    ),
    PortalEntity(
        "schedules",
        EmployeeSchedule,
        PortalEmployeeScheduleForm,
        ("user", "schedule_type", "is_active", "updated_at"),
        "Графики сотрудников",
    ),
)
PORTAL_BY_SLUG = {e.slug: e for e in PORTAL_ENTITIES}

# Разделы с отдельными страницами в основном меню — не показываем повторно на главной портала.
_PORTAL_SLUGS_IN_MAIN_NAV = frozenset({"equipment", "requests", "workplaces", "cabinets"})


PORTAL_ENTITY_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "equipment": ("warehouse_operations",),
    "categories": ("warehouse_operations",),
    "workplaces": ("warehouse_operations",),
    "cabinets": ("warehouse_operations",),
    "requests": ("request_processing", "users_and_site_admin"),
    "periodic-usage": ("usage_writeoff", "warehouse_operations", "request_creation", "users_and_site_admin"),
    "users": ("users_and_site_admin",),
    "groups": ("users_and_site_admin",),
    "registration-domains": ("users_and_site_admin",),
    "schedules": ("warehouse_operations", "users_and_site_admin"),
}


def _can_access_portal_entity(user, slug: str) -> bool:
    capabilities = PORTAL_ENTITY_CAPABILITIES.get(slug, ())
    return any(user_has_capability(user, capability) for capability in capabilities)


def _can_modify_portal_entity(user, slug: str) -> bool:
    # "Периодический расход" для админа показываем в режиме чтения.
    if slug == "periodic-usage":
        return (
            user_has_capability(user, "usage_writeoff")
            or user_has_capability(user, "warehouse_operations")
            or user_has_capability(user, "request_creation")
        )
    return _can_access_portal_entity(user, slug)


def _visible_portal_entities(user):
    return tuple(entity for entity in PORTAL_ENTITIES if _can_access_portal_entity(user, entity.slug))


def _portal_dashboard_sections(user):
    return [
        {"title": e.title, "url": _portal_section_list_url(e.slug)}
        for e in _visible_portal_entities(user)
        if e.slug not in _PORTAL_SLUGS_IN_MAIN_NAV
    ]


def _portal_nav_context(user, current_slug: str | None = None):
    return {"entities": _visible_portal_entities(user), "current_entity_slug": current_slug}


def _portal_section_list_url(entity_slug: str) -> str:
    route = {
        "equipment": "equipment_list",
        "requests": "request_history",
        "workplaces": "workplaces",
        "cabinets": "cabinets",
    }
    name = route.get(entity_slug)
    if name:
        return reverse(name)
    return reverse("portal_list", kwargs={"entity": entity_slug})


def _redirect_to_portal_section_list(entity_slug: str):
    route = {
        "equipment": ("equipment_list", {}),
        "requests": ("request_history", {}),
        "workplaces": ("workplaces", {}),
        "cabinets": ("cabinets", {}),
    }
    target = route.get(entity_slug)
    if target:
        return redirect(target[0], **target[1])
    return redirect("portal_list", entity=entity_slug)


def _portal_common_context(user, current_slug: str | None = None):
    ctx = {
        **_portal_nav_context(user, current_slug),
        "yandex_maps_api_key": getattr(settings, "YANDEX_MAPS_API_KEY", ""),
    }
    if current_slug and current_slug not in ("__home", "__logs"):
        ctx["portal_back_url"] = _portal_section_list_url(current_slug)
        ctx["portal_back_label"] = _("К списку")
    return ctx


def _portal_confirm_context(user, entity_slug: str):
    return {
        **_portal_nav_context(user, entity_slug),
        "portal_back_url": _portal_section_list_url(entity_slug),
        "portal_back_label": _("К списку"),
    }


def _procedure_cards(user):
    cards = []
    if user_has_capability(user, "users_and_site_admin"):
        cards.extend(
            [
                {
                    "slug": "reject_stale_requests",
                    "title": _("Отклонить старые заявки"),
                    "description": _("Помечает старые необработанные заявки как отклонённые и фиксирует, кто их обработал."),
                    "form": RejectStaleRequestsProcedureForm(prefix="reject"),
                },
                {
                    "slug": "restock_low_stock_consumables",
                    "title": _("Пополнить расходники с низким остатком"),
                    "description": _(
                        "Создаёт корректировки остатков для расходников ниже порога; каждая позиция увеличивается на фиксированное число единиц."
                    ),
                    "form": RestockLowStockConsumablesProcedureForm(prefix="restock"),
                },
            ]
        )
    return cards


def _manager(model):
    return getattr(model, "all_objects", model.objects)


def _portal_guard(request, entity_slug: str | None = None):
    if entity_slug:
        if not _can_access_portal_entity(request.user, entity_slug):
            return forbidden(request, "Этот раздел портала вам недоступен.")
        return None
    if not _visible_portal_entities(request.user):
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
    if "inventory_number" in text or "serial_number" in text:
        return _("Такой серийный номер уже существует. Укажите другой.")
    return _("Не удалось сохранить запись из-за дублирующегося или некорректного уникального значения.")


@login_required
def portal_dashboard(request):
    if resp := _portal_guard(request):
        return resp
    portal_sections = _portal_dashboard_sections(request.user)
    return render(
        request,
        "inventory/portal/dashboard.html",
        {
            **_portal_nav_context(request.user, "__home"),
            "portal_sections": portal_sections,
            "procedure_cards": _procedure_cards(request.user),
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
            **_portal_nav_context(request.user, "__logs"),
            "logs": logs,
        },
    )


@login_required
def portal_list(request, entity: str):
    cfg = _get_entity_or_404(entity)
    if resp := _portal_guard(request, cfg.slug):
        return resp
    if cfg.slug == "requests":
        return redirect("request_history")
    if cfg.slug == "equipment":
        return redirect("equipment_list")
    if cfg.slug == "workplaces":
        return redirect("workplaces")
    if cfg.slug == "cabinets":
        return redirect("cabinets")
    show_deleted = bool(request.session.get("show_deleted_global", False))
    has_soft_delete = any(f.name == "deleted_at" for f in cfg.model._meta.fields)
    qs = _manager(cfg.model).all()
    if has_soft_delete and not show_deleted:
        qs = qs.filter(deleted_at__isnull=True)
    ordering = getattr(cfg.model._meta, "ordering", None) or ("-pk",)
    qs = qs.order_by(*ordering)
    preferences = _get_user_preferences(request.user)
    page_size = preferences.page_size if preferences else 25
    page_obj = _paginate(request, qs, page_size)
    return render(
        request,
        "inventory/portal/object_list.html",
        {
            **_portal_nav_context(request.user, cfg.slug),
            "cfg": cfg,
            "objects": page_obj.object_list,
            "can_manage_entity": _can_modify_portal_entity(request.user, cfg.slug),
            "show_deleted": show_deleted,
            "has_soft_delete": has_soft_delete,
            "list_headers": _list_headers(cfg.model, cfg.list_fields),
            **_with_page_context(page_obj),
        },
    )


@login_required
def portal_create(request, entity: str):
    cfg = _get_entity_or_404(entity)
    if resp := _portal_guard(request, cfg.slug):
        return resp
    if not _can_modify_portal_entity(request.user, cfg.slug):
        return forbidden(request, "В этом разделе вам доступен только просмотр.")
    Form = cfg.form_class
    if request.method == "POST":
        form = Form(request.POST, request.FILES)
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                if hasattr(obj, "_actor"):
                    obj._actor = request.user
                if isinstance(obj, PeriodicMaterialUsageSchedule) and obj.pk is None:
                    obj.created_by = request.user
                obj.save()
                if hasattr(form, "save_m2m"):
                    form.save_m2m()
                log_portal_action(request, "create", cfg.slug, obj=obj, meta={"pk": obj.pk})
                messages.success(request, f"Запись добавлена: {cfg.title}.")
                return _redirect_to_portal_section_list(cfg.slug)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            except IntegrityError as exc:
                form.add_error(None, _friendly_integrity_message(exc))
    else:
        form = Form()
    extra_context = {}
    if cfg.slug == "periodic-usage":
        extra_context["equipment_photo_map"] = {
            str(eq.pk): eq.photo.url
            for eq in Equipment.objects.filter(is_consumable=True, deleted_at__isnull=True).only("id", "photo")
            if eq.photo
        }
    if cfg.slug == "equipment":
        extra_context["can_quick_add_equipment_refs"] = user_has_capability(request.user, "warehouse_operations")
        extra_context["equipment_category_add_url"] = reverse("portal_create", kwargs={"entity": "categories"})
    return render(
        request,
        "inventory/portal/object_form.html",
        {**_portal_common_context(request.user, cfg.slug), "cfg": cfg, "form": form, "is_edit": False, **extra_context},
    )


@login_required
def portal_edit(request, entity: str, pk: int):
    cfg = _get_entity_or_404(entity)
    if resp := _portal_guard(request, cfg.slug):
        return resp
    if not _can_modify_portal_entity(request.user, cfg.slug):
        return forbidden(request, "В этом разделе вам доступен только просмотр.")
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
                messages.success(request, f"Запись обновлена: {cfg.title}.")
                return _redirect_to_portal_section_list(cfg.slug)
            except ValidationError as exc:
                form.add_error(None, "; ".join(exc.messages))
            except IntegrityError as exc:
                form.add_error(None, _friendly_integrity_message(exc))
    else:
        form = Form(instance=obj)
    extra_context = {}
    if cfg.slug == "periodic-usage":
        extra_context["equipment_photo_map"] = {
            str(eq.pk): eq.photo.url
            for eq in Equipment.objects.filter(is_consumable=True, deleted_at__isnull=True).only("id", "photo")
            if eq.photo
        }
    if cfg.slug == "equipment":
        extra_context["can_quick_add_equipment_refs"] = user_has_capability(request.user, "warehouse_operations")
        extra_context["equipment_category_add_url"] = reverse("portal_create", kwargs={"entity": "categories"})
    return render(
        request,
        "inventory/portal/object_form.html",
        {
            **_portal_common_context(request.user, cfg.slug),
            "cfg": cfg,
            "form": form,
            "is_edit": True,
            "object": obj,
            **extra_context,
        },
    )


@login_required
def portal_delete(request, entity: str, pk: int):
    cfg = _get_entity_or_404(entity)
    if resp := _portal_guard(request, cfg.slug):
        return resp
    if not _can_modify_portal_entity(request.user, cfg.slug):
        return forbidden(request, "В этом разделе вам доступен только просмотр.")
    obj = get_object_or_404(_manager(cfg.model), pk=pk)
    if cfg.model is User and obj.pk == request.user.pk:
        messages.error(request, "Нельзя удалить собственную учётную запись.")
        return _redirect_to_portal_section_list(cfg.slug)
    if request.method == "POST":
        if hasattr(obj, "_actor"):
            obj._actor = request.user
        obj_repr = str(obj)
        obj_pk = obj.pk
        try:
            obj.delete()
            log_portal_action(request, "delete", cfg.slug, obj=obj_repr, meta={"pk": obj_pk})
            messages.success(request, f"Запись удалена: {cfg.title}.")
            return _redirect_to_portal_section_list(cfg.slug)
        except ProtectedError:
            messages.error(request, _("Эту запись нельзя удалить, потому что она используется связанными данными."))
            return _redirect_to_portal_section_list(cfg.slug)
    return render(
        request,
        "inventory/portal/object_confirm_delete.html",
        {**_portal_confirm_context(request.user, cfg.slug), "cfg": cfg, "object": obj},
    )


@login_required
def portal_restore(request, entity: str, pk: int):
    cfg = _get_entity_or_404(entity)
    if resp := _portal_guard(request, cfg.slug):
        return resp
    if not _can_modify_portal_entity(request.user, cfg.slug):
        return forbidden(request, "В этом разделе вам доступен только просмотр.")
    if not hasattr(cfg.model, "restore"):
        from django.http import Http404

        raise Http404()
    obj = get_object_or_404(cfg.model.all_objects, pk=pk)
    if request.method == "POST":
        obj.restore()
        log_portal_action(request, "restore", cfg.slug, obj=obj, meta={"pk": obj.pk})
        messages.success(request, f"Запись восстановлена: {cfg.title}.")
        return _redirect_to_portal_section_list(cfg.slug)
    return render(
        request,
        "inventory/portal/object_confirm_restore.html",
        {**_portal_confirm_context(request.user, cfg.slug), "cfg": cfg, "object": obj},
    )


@login_required
def portal_procedure_run(request, slug: str):
    if resp := _portal_guard(request):
        return resp
    if request.method != "POST":
        return redirect("portal_home")

    extra_meta: dict = {}

    if slug == "reject_stale_requests":
        if not user_has_capability(request.user, "users_and_site_admin"):
            return forbidden(request, "Запуск этой процедуры доступен только администратору.")
        form = RejectStaleRequestsProcedureForm(request.POST, prefix="reject")
        if not form.is_valid():
            messages.error(request, _("Укажите корректный срок давности для заявок."))
            return redirect("portal_home")
        extra_meta["stale_days"] = form.cleaned_data["stale_days"]
        result = reject_stale_requests(actor=request.user, stale_days=form.cleaned_data["stale_days"])
    elif slug == "restock_low_stock_consumables":
        if not user_has_capability(request.user, "users_and_site_admin"):
            return forbidden(request, "Запуск этой процедуры доступен только администратору.")
        form = RestockLowStockConsumablesProcedureForm(request.POST, prefix="restock")
        if not form.is_valid():
            messages.error(request, _("Укажите фиксированное число единиц для пополнения (не меньше 1)."))
            return redirect("portal_home")
        extra_meta["fixed_increase"] = form.cleaned_data["fixed_increase"]
        result = restock_low_stock_consumables(actor=request.user, fixed_increase=form.cleaned_data["fixed_increase"])
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
            **extra_meta,
        },
    )
    messages.success(request, result.detail)
    return redirect("portal_home")
