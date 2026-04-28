from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import Cabinet, EquipmentCategory, Workplace, WorkplaceMember
from operations.models import EquipmentRequest, MaterialUsage

from .models import AuditLog

TRACKED_MODELS = (
    EquipmentCategory,
    Workplace,
    Cabinet,
    WorkplaceMember,
    Equipment,
    InventoryAdjustment,
    EquipmentCheckout,
    EquipmentRequest,
    MaterialUsage,
)

IGNORED_FIELDS = {"updated_at"}


def _get_manager(model):
    return getattr(model, "all_objects", model.objects)


def _get_actor(instance):
    return getattr(instance, "_actor", None)


def _diff(prev, instance):
    if not prev:
        return {}
    changes = {}
    for field in instance._meta.fields:
        name = field.name
        if name in IGNORED_FIELDS:
            continue
        old = getattr(prev, name)
        new = getattr(instance, name)
        if old != new:
            changes[name] = {"from": str(old), "to": str(new)}
    return changes


def _log_action(instance, action, meta=None):
    AuditLog.objects.create(
        action=action,
        actor=_get_actor(instance),
        content_type=ContentType.objects.get_for_model(instance, for_concrete_model=False),
        object_id=str(instance.pk),
        object_repr=str(instance)[:200],
        meta=meta or {},
    )


def _pre_save(sender, instance, **kwargs):
    if not instance.pk:
        instance._audit_prev = None
        return
    manager = _get_manager(sender)
    instance._audit_prev = manager.filter(pk=instance.pk).first()


def _post_save(sender, instance, created, **kwargs):
    prev = getattr(instance, "_audit_prev", None)
    if created:
        _log_action(instance, "created")
        return
    if prev and not prev.deleted_at and instance.deleted_at:
        _log_action(instance, "deleted")
        return
    changes = _diff(prev, instance)
    if changes:
        _log_action(instance, "updated", {"changes": changes})


for model in TRACKED_MODELS:
    pre_save.connect(_pre_save, sender=model, dispatch_uid=f"audit_pre_save_{model.__name__}")
    post_save.connect(_post_save, sender=model, dispatch_uid=f"audit_post_save_{model.__name__}")
