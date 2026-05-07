from dataclasses import dataclass
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from assets.models import Equipment, InventoryAdjustment, STATUS_IN_STOCK
from operations.models import (
    REQUEST_PENDING,
    REQUEST_REJECTED,
    EquipmentRequest,
)


@dataclass(frozen=True)
class ProcedureResult:
    slug: str
    title: str
    processed_count: int
    detail: str
    execution_mode: str = "orm"


def _is_postgresql() -> bool:
    return connection.vendor == "postgresql"


def _set_db_actor(actor_id: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('app.current_actor_id', %s, true)", [str(actor_id)])


def _append_reason(existing: str, addition: str) -> str:
    existing = (existing or "").strip()
    if not existing:
        return addition
    if addition in existing:
        return existing
    return f"{existing}\n{addition}"


def reject_stale_requests(*, actor, stale_days: int) -> ProcedureResult:
    cutoff = timezone.now() - timedelta(days=stale_days)
    note = f"Отклонено автоматически админ-процедурой после {stale_days} дн."
    processed = EquipmentRequest.objects.filter(
        status=REQUEST_PENDING,
        requested_at__lt=cutoff,
    ).count()

    if _is_postgresql():
        with transaction.atomic():
            _set_db_actor(actor.pk)
            with connection.cursor() as cursor:
                cursor.execute("CALL reject_stale_requests(%s, %s)", [actor.pk, stale_days])
        return ProcedureResult(
            slug="reject_stale_requests",
            title="Отклонение старых заявок",
            processed_count=processed,
            detail=f"Отклонено заявок в статусе «Ожидает»: {processed} (старше {stale_days} дн.).",
            execution_mode="postgresql_procedure",
        )

    processed = 0
    with transaction.atomic():
        for request in EquipmentRequest.objects.select_for_update().filter(status=REQUEST_PENDING, requested_at__lt=cutoff):
            request.status = REQUEST_REJECTED
            request.processed_by = actor
            request.processed_at = timezone.now()
            request.comment = _append_reason(request.comment, note)
            request._actor = actor
            request.save(update_fields=["status", "processed_by", "processed_at", "comment"])
            processed += 1

    return ProcedureResult(
        slug="reject_stale_requests",
        title="Отклонение старых заявок",
        processed_count=processed,
        detail=f"Отклонено заявок в статусе «Ожидает»: {processed} (старше {stale_days} дн.).",
    )


def restock_low_stock_consumables(*, actor, fixed_increase: int = 1) -> ProcedureResult:
    fixed_increase = max(1, int(fixed_increase))
    processed = Equipment.objects.filter(
        is_consumable=True,
        low_stock_threshold__gt=0,
        quantity_available__lt=F("low_stock_threshold"),
    ).count()

    reason = f"Автопополнение по процедуре: +{fixed_increase} шт."

    processed = 0
    with transaction.atomic():
        low_stock_items = Equipment.objects.select_for_update().filter(
            is_consumable=True,
            low_stock_threshold__gt=0,
            quantity_available__lt=F("low_stock_threshold"),
        )

        for equipment in low_stock_items:
            delta = fixed_increase
            adjustment = InventoryAdjustment(
                equipment=equipment,
                delta=delta,
                reason=reason,
                created_by=actor,
            )
            adjustment._actor = actor
            adjustment.save()
            if equipment.status != STATUS_IN_STOCK:
                equipment.status = STATUS_IN_STOCK
                equipment._actor = actor
                equipment.save(update_fields=["status"])
            processed += 1

    detail = f"Пополнено расходников с низким остатком: {processed} поз., по +{fixed_increase} шт."
    return ProcedureResult(
        slug="restock_low_stock_consumables",
        title="Пополнение расходников с низким остатком",
        processed_count=processed,
        detail=detail,
    )


def simple_restock_and_recover_equipment(
    *,
    actor,
    equipment_id: int,
    quantity: int = 1,
    non_consumable_action: str = "set_in_stock",
) -> ProcedureResult:
    quantity = max(1, int(quantity))
    processed = 0

    with transaction.atomic():
        equipment = Equipment.objects.select_for_update().get(pk=equipment_id, deleted_at__isnull=True)
        if equipment.is_consumable:
            adjustment = InventoryAdjustment(
                equipment=equipment,
                delta=quantity,
                reason=f"Ручное пополнение по процедуре: +{quantity} шт.",
                created_by=actor,
            )
            adjustment._actor = actor
            adjustment.save()
            processed += 1
            detail = f"Расходник «{equipment.name}» пополнен на {quantity} шт."
        else:
            if non_consumable_action == "set_in_stock":
                equipment.status = STATUS_IN_STOCK
                equipment._actor = actor
                equipment.save(update_fields=["status"])
                processed += 1
                detail = f"Нерасходник «{equipment.name}» переведён в статус «На складе»."
            else:
                adjustment = InventoryAdjustment(
                    equipment=equipment,
                    delta=quantity,
                    reason=f"Ручное пополнение нерасходника по процедуре: +{quantity} шт.",
                    created_by=actor,
                )
                adjustment._actor = actor
                adjustment.save()
                processed += 1
                detail = f"Нерасходник «{equipment.name}» пополнен на {quantity} шт."

    return ProcedureResult(
        slug="simple_restock_and_recover_equipment",
        title="Простое пополнение и возврат на склад",
        processed_count=processed,
        detail=detail,
    )


PROCEDURE_REGISTRY = {
    "reject_stale_requests": reject_stale_requests,
    "restock_low_stock_consumables": restock_low_stock_consumables,
    "simple_restock_and_recover_equipment": simple_restock_and_recover_equipment,
}
