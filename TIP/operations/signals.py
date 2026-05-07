from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.db import transaction

from assets.models import Equipment, STATUS_RETIRED
from core.async_tasks import run_in_background
from core.message_email_notify import (
    notify_direct_message_email,
    notify_request_approved_email,
    notify_request_message_subscribers,
)
from core.models import DirectMessage

from .models import (
    REQUEST_APPROVED,
    EquipmentRequest,
    EquipmentRequestMessage,
    EquipmentRequestPhoto,
    MaterialUsage,
    REQUEST_KIND_RESTOCK,
)


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
    # Остаток обнулился — позиция на складе исчерпана, статус «Списано».
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
def notify_email_on_request_approved(sender, instance, created, **kwargs):
    if created:
        return
    if instance.status != REQUEST_APPROVED:
        return
    previous_status = getattr(instance, "_previous_status", None)
    if previous_status == REQUEST_APPROVED:
        return
    approver_username = None
    if instance.processed_by_id:
        approver_username = instance.processed_by.get_username()
    transaction.on_commit(
        lambda: run_in_background(
            notify_request_approved_email,
            instance.requester,
            request_id=instance.pk,
            approver_username=approver_username,
            equipment_name=instance.equipment.name if instance.equipment_id and instance.equipment else None,
            needed_by=instance.needed_by,
            processed_at=instance.processed_at,
        )
    )


@receiver(post_save, sender=EquipmentRequest)
def auto_consume_consumable_on_request_approval(sender, instance, created, **kwargs):
    if created or instance.status != REQUEST_APPROVED:
        return
    previous_status = getattr(instance, "_previous_status", None)
    if previous_status == REQUEST_APPROVED:
        return
    if not instance.equipment_id or instance.quantity <= 0:
        return
    if instance.request_kind == REQUEST_KIND_RESTOCK:
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


@receiver(post_save, sender=DirectMessage)
def notify_email_on_direct_message(sender, instance, created, **kwargs):
    if not created:
        return
    run_in_background(
        notify_direct_message_email,
        instance.recipient,
        sender_username=instance.sender.get_username(),
        sender_id=instance.sender_id,
        body=instance.body,
    )


@receiver(post_save, sender=EquipmentRequestMessage)
def notify_email_on_request_message(sender, instance, created, **kwargs):
    if not created:
        return
    message_pk = instance.pk
    request_id = instance.request_id
    author_id = instance.author_id

    def _notify() -> None:
        msg = (
            EquipmentRequestMessage.objects.select_related("author")
            .filter(pk=message_pk)
            .first()
        )
        if not msg:
            return
        req = (
            EquipmentRequest.objects.select_related("requester", "processed_by")
            .filter(pk=request_id)
            .first()
        )
        if not req:
            return
        raw_body = (msg.body or "").strip()
        has_photos = EquipmentRequestPhoto.objects.filter(message_id=message_pk).exists()
        display_body = raw_body
        if not display_body and has_photos:
            display_body = "Вложено изображение — откройте заявку в системе, чтобы посмотреть."
        elif not display_body:
            display_body = "(без текста)"
        notify_request_message_subscribers(
            request_id=request_id,
            author_id=author_id,
            author_username=msg.author.get_username(),
            body=display_body,
            requester=req.requester,
            processed_by=req.processed_by,
            automation_body=raw_body,
            equipment_name=req.equipment.name if req.equipment_id and req.equipment else None,
            needed_by=req.needed_by,
            message_created_at=msg.created_at,
        )

    transaction.on_commit(lambda: run_in_background(_notify))
