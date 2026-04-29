from dataclasses import dataclass
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from assets.models import Equipment, InventoryAdjustment
from operations.models import (
    REQUEST_CLOSED,
    REQUEST_ISSUED,
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
        title="Reject stale requests",
        processed_count=processed,
        detail=f"Rejected {processed} pending request(s) older than {stale_days} day(s).",
    )


def restock_low_stock_consumables(*, actor, target_addon: int = 0) -> ProcedureResult:
    target_addon = max(0, int(target_addon))
    processed = Equipment.objects.filter(
        is_consumable=True,
        low_stock_threshold__gt=0,
        quantity_available__lt=F("low_stock_threshold"),
    ).count()

    reason = (
        f"Automatic restock to low-stock threshold plus {target_addon} by admin procedure."
        if target_addon
        else "Automatic restock to low-stock threshold by admin procedure."
    )

    if _is_postgresql():
        with transaction.atomic():
            _set_db_actor(actor.pk)
            with connection.cursor() as cursor:
                cursor.execute("CALL restock_low_stock_consumables(%s, %s)", [actor.pk, target_addon])
        detail = f"Created {processed} restock adjustment(s) for low-stock consumables."
        if target_addon:
            detail += f" Целевой остаток: порог + {target_addon}."
        return ProcedureResult(
            slug="restock_low_stock_consumables",
            title="Restock low-stock consumables",
            processed_count=processed,
            detail=detail,
            execution_mode="postgresql_procedure",
        )

    processed = 0
    with transaction.atomic():
        low_stock_items = Equipment.objects.select_for_update().filter(
            is_consumable=True,
            low_stock_threshold__gt=0,
            quantity_available__lt=F("low_stock_threshold"),
        )

        for equipment in low_stock_items:
            target_quantity = equipment.low_stock_threshold + target_addon
            if equipment.quantity_available >= target_quantity:
                continue
            delta = target_quantity - equipment.quantity_available
            adjustment = InventoryAdjustment(
                equipment=equipment,
                delta=delta,
                reason=reason,
                created_by=actor,
            )
            adjustment._actor = actor
            adjustment.save()
            processed += 1

    detail = f"Created {processed} restock adjustment(s) for low-stock consumables."
    if target_addon:
        detail += f" Целевой остаток: порог + {target_addon}."
    return ProcedureResult(
        slug="restock_low_stock_consumables",
        title="Restock low-stock consumables",
        processed_count=processed,
        detail=detail,
    )


def close_stale_issued_requests(*, actor, stale_days: int) -> ProcedureResult:
    cutoff = timezone.now() - timedelta(days=stale_days)
    note = f"Closed automatically by admin procedure after {stale_days} day(s) in issued status."
    stale_filter = Q(status=REQUEST_ISSUED) & (
        Q(processed_at__lt=cutoff) | Q(processed_at__isnull=True, requested_at__lt=cutoff)
    )
    processed = 0
    with transaction.atomic():
        for request in EquipmentRequest.objects.select_for_update().filter(stale_filter):
            request.status = REQUEST_CLOSED
            if not request.processed_by_id:
                request.processed_by = actor
            request.processed_at = timezone.now()
            request.comment = _append_reason(request.comment, note)
            request._actor = actor
            request.save(update_fields=["status", "processed_by", "processed_at", "comment"])
            processed += 1

    return ProcedureResult(
        slug="close_stale_issued_requests",
        title="Close stale issued requests",
        processed_count=processed,
        detail=f"Closed {processed} issued request(s) older than {stale_days} day(s).",
    )


PROCEDURE_REGISTRY = {
    "reject_stale_requests": reject_stale_requests,
    "restock_low_stock_consumables": restock_low_stock_consumables,
    "close_stale_issued_requests": close_stale_issued_requests,
}
