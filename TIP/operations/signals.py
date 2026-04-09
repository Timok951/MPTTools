from django.db.models import F
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import MaterialUsage


@receiver(post_save, sender=MaterialUsage)
def apply_material_usage(sender, instance, created, **kwargs):
    if not created or not instance.equipment_id:
        return
    instance.equipment.__class__.objects.filter(pk=instance.equipment_id).update(
        quantity_available=F("quantity_available") - instance.quantity,
    )
