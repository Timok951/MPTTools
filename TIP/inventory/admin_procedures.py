from dataclasses import dataclass
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from assets.models import Equipment, InventoryAdjustment
from operations.models import REQUEST_PENDING, REQUEST_REJECTED, EquipmentRequest, WorkTimer


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
    note = f"Rejected automatically by admin procedure after {stale_days} days."
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
            title="Reject stale requests",
            processed_count=processed,
            detail=f"Rejected {processed} pending request(s) older than {stale_days} day(s).",
            execution_mode="postgresql_procedure",
        )

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
        title="Reject stale requests",
        processed_count=processed,
        detail=f"Rejected {processed} pending request(s) older than {stale_days} day(s).",
    )


def finish_abandoned_timers(*, actor, stale_hours: int) -> ProcedureResult:
    cutoff = timezone.now() - timedelta(hours=stale_hours)
    processed = WorkTimer.objects.filter(
        ended_at__isnull=True,
        started_at__lt=cutoff,
    ).count()

    if _is_postgresql():
        with transaction.atomic():
            _set_db_actor(actor.pk)
            with connection.cursor() as cursor:
                cursor.execute("CALL finish_abandoned_timers(%s, %s)", [actor.pk, stale_hours])
        return ProcedureResult(
            slug="finish_abandoned_timers",
            title="Finish abandoned timers",
            processed_count=processed,
            detail=f"Finished {processed} active timer(s) older than {stale_hours} hour(s).",
            execution_mode="postgresql_procedure",
        )

    with transaction.atomic():
        for timer in WorkTimer.objects.select_for_update().filter(
            ended_at__isnull=True,
            started_at__lt=cutoff,
        ):
            timer.ended_at = timezone.now()
            timer.note = _append_reason(
                timer.note,
                f"Finished automatically by admin procedure after {stale_hours} hour(s).",
            )
            timer._actor = actor
            timer.save(update_fields=["ended_at", "note"])
            processed += 1

    return ProcedureResult(
        slug="finish_abandoned_timers",
        title="Finish abandoned timers",
        processed_count=processed,
        detail=f"Finished {processed} active timer(s) older than {stale_hours} hour(s).",
    )


def restock_low_stock_consumables(*, actor) -> ProcedureResult:
    processed = Equipment.objects.filter(
        is_consumable=True,
        low_stock_threshold__gt=0,
        quantity_available__lt=F("low_stock_threshold"),
    ).count()

    if _is_postgresql():
        with transaction.atomic():
            _set_db_actor(actor.pk)
            with connection.cursor() as cursor:
                cursor.execute("CALL restock_low_stock_consumables(%s)", [actor.pk])
        return ProcedureResult(
            slug="restock_low_stock_consumables",
            title="Restock low-stock consumables",
            processed_count=processed,
            detail=f"Created {processed} restock adjustment(s) for low-stock consumables.",
            execution_mode="postgresql_procedure",
        )

    processed = 0
    with transaction.atomic():
        low_stock_items = Equipment.objects.select_for_update().filter(
            is_consumable=True,
            low_stock_threshold__gt=0,
        )

        for equipment in low_stock_items:
            target_quantity = max(equipment.low_stock_threshold, 0)
            if equipment.quantity_available >= target_quantity:
                continue
            delta = target_quantity - equipment.quantity_available
            adjustment = InventoryAdjustment(
                equipment=equipment,
                delta=delta,
                reason="Automatic restock to low-stock threshold by admin procedure.",
                created_by=actor,
            )
            adjustment._actor = actor
            adjustment.save()
            processed += 1

    return ProcedureResult(
        slug="restock_low_stock_consumables",
        title="Restock low-stock consumables",
        processed_count=processed,
        detail=f"Created {processed} restock adjustment(s) for low-stock consumables.",
    )


PROCEDURE_REGISTRY = {
    "reject_stale_requests": reject_stale_requests,
    "finish_abandoned_timers": finish_abandoned_timers,
    "restock_low_stock_consumables": restock_low_stock_consumables,
}
