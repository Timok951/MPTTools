from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from assets.models import Equipment, STATUS_RETIRED

from .models import REQUEST_APPROVED, EquipmentRequest, MaterialUsage


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


@receiver(pre_save, sender=EquipmentRequest)
def stash_request_previous_status(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_status = None
        return
    previous = sender.objects.filter(pk=instance.pk).only("status").first()
    instance._previous_status = previous.status if previous else None


@receiver(post_save, sender=EquipmentRequest)
def auto_consume_consumable_on_request_approval(sender, instance, created, **kwargs):
    if created or instance.status != REQUEST_APPROVED:
        return
    previous_status = getattr(instance, "_previous_status", None)
    if previous_status == REQUEST_APPROVED:
        return
    if not instance.equipment_id or instance.quantity <= 0:
        return
    equipment = instance.equipment
    if not equipment or not equipment.is_consumable:
        return
    already_consumed = MaterialUsage.objects.filter(
        related_request_id=instance.pk,
        note__startswith="Авторасход по одобренной заявке",
    ).exists()
    if already_consumed:
        return
    MaterialUsage.objects.create(
        equipment=equipment,
        workplace=instance.workplace,
        quantity=instance.quantity,
        used_by=instance.processed_by or instance.requester,
        related_request=instance,
        note=f"Авторасход по одобренной заявке #{instance.pk}",
    )
