from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import MaterialUsage


@receiver(post_save, sender=MaterialUsage)
def apply_material_usage(sender, instance, created, **kwargs):
    if not created or not instance.equipment_id:
        return
    # "Склад" ведём по quantity_total; available удерживаем неотрицательным,
    # чтобы не падать на БД-ограничении при старых неконсистентных остатках.
    instance.equipment.__class__.objects.filter(pk=instance.equipment_id).update(
        quantity_total=Greatest(F("quantity_total") - instance.quantity, Value(0)),
        quantity_available=Greatest(F("quantity_available") - instance.quantity, Value(0)),
    )
