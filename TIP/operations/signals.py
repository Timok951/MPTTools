from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.db.models.signals import post_save
from django.dispatch import receiver

from assets.models import Equipment, STATUS_RETIRED

from .models import MaterialUsage


@receiver(post_save, sender=MaterialUsage)
def apply_material_usage(sender, instance, created, **kwargs):
    if not created or not instance.equipment_id:
        return
    # "Склад" ведём по quantity_total; available удерживаем неотрицательным,
    # чтобы не падать на БД-ограничении при старых неконсистентных остатках.
    Equipment.objects.filter(pk=instance.equipment_id).update(
        quantity_total=Greatest(F("quantity_total") - instance.quantity, Value(0)),
        quantity_available=Greatest(F("quantity_available") - instance.quantity, Value(0)),
    )
    # Полное списание по количеству — позиция на складе исчерпана, статус «Списано».
    Equipment.objects.filter(
        pk=instance.equipment_id,
        quantity_total=0,
        quantity_available=0,
    ).exclude(status=STATUS_RETIRED).update(status=STATUS_RETIRED)
