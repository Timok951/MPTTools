from django.db.models import F
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import EquipmentCheckout, InventoryAdjustment


@receiver(post_save, sender=InventoryAdjustment)
def apply_inventory_adjustment(sender, instance, created, **kwargs):
    if not created or not instance.equipment_id:
        return
    instance.equipment.__class__.objects.filter(pk=instance.equipment_id).update(
        quantity_total=F("quantity_total") + instance.delta,
        quantity_available=F("quantity_available") + instance.delta,
    )


@receiver(pre_save, sender=EquipmentCheckout)
def stash_checkout_prev(sender, instance, **kwargs):
    if not instance.pk:
        instance._prev_state = None
        return
    instance._prev_state = sender.all_objects.filter(pk=instance.pk).first()


@receiver(post_save, sender=EquipmentCheckout)
def apply_checkout_effect(sender, instance, created, **kwargs):
    if not instance.equipment_id:
        return
    if created and not instance.returned_at:
        sender_equipment = instance.equipment.__class__
        sender_equipment.objects.filter(pk=instance.equipment_id).update(
            quantity_available=F("quantity_available") - instance.quantity,
        )
        if instance.related_request_id:
            from operations.models import REQUEST_ISSUED, EquipmentRequest

            EquipmentRequest.objects.filter(pk=instance.related_request_id).update(status=REQUEST_ISSUED)
        return
    prev = getattr(instance, "_prev_state", None)
    if prev and not prev.returned_at and instance.returned_at:
        sender_equipment = instance.equipment.__class__
        sender_equipment.objects.filter(pk=instance.equipment_id).update(
            quantity_available=F("quantity_available") + prev.quantity,
        )
        if instance.related_request_id:
            from operations.models import REQUEST_CLOSED, EquipmentRequest

            EquipmentRequest.objects.filter(pk=instance.related_request_id).update(status=REQUEST_CLOSED)
