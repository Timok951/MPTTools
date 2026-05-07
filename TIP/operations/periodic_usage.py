"""Логика периодических расписаний: создаём заявки на рассмотрение."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from .models import (
    EquipmentRequest,
    PeriodicMaterialUsageSchedule,
    PERIODIC_USAGE_BIWEEKLY,
    PERIODIC_USAGE_DAILY,
    PERIODIC_USAGE_MONTHLY,
    PERIODIC_USAGE_QUARTERLY,
    PERIODIC_USAGE_WEEKLY,
    PERIODIC_USAGE_YEARLY,
    REQUEST_KIND_WRITEOFF,
    REQUEST_PENDING,
)


def add_one_calendar_month(d: date) -> date:
    m = d.month + 1
    y = d.year
    if m > 12:
        m = 1
        y += 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def add_months(d: date, months: int) -> date:
    result = d
    for _ in range(max(0, months)):
        result = add_one_calendar_month(result)
    return result


def add_by_frequency(d: date, frequency: str) -> date:
    if frequency == PERIODIC_USAGE_DAILY:
        return d + timedelta(days=1)
    if frequency == PERIODIC_USAGE_WEEKLY:
        return d + timedelta(days=7)
    if frequency == PERIODIC_USAGE_BIWEEKLY:
        return d + timedelta(days=14)
    if frequency == PERIODIC_USAGE_QUARTERLY:
        return add_months(d, 3)
    if frequency == PERIODIC_USAGE_YEARLY:
        return add_months(d, 12)
    # monthly by default (and fallback for legacy/unknown values)
    return add_one_calendar_month(d)


def process_due_periodic_schedules(*, today: date | None = None) -> int:
    """Создаёт заявки на расход для всех просроченных периодических расписаний."""
    if today is None:
        today = timezone.localdate()
    created = 0
    manager = PeriodicMaterialUsageSchedule.objects
    due_ids = list(
        manager.filter(is_active=True, deleted_at__isnull=True, next_run_on__lte=today).values_list("pk", flat=True)
    )
    for pk in due_ids:
        with transaction.atomic():
            schedule = (
                PeriodicMaterialUsageSchedule.objects.select_for_update()
                .select_related("equipment", "workplace", "created_by")
                .filter(pk=pk, is_active=True, deleted_at__isnull=True)
                .first()
            )
            if not schedule or schedule.next_run_on > today:
                continue
            while schedule.next_run_on <= today:
                if not schedule.created_by_id:
                    break
                note = schedule.title.strip() if schedule.title else ""
                prefix = f"Периодическая заявка #{schedule.pk}"
                full_note = f"{prefix}" + (f": {note}" if note else "")
                EquipmentRequest.objects.create(
                    requester=schedule.created_by,
                    workplace=schedule.workplace,
                    equipment=schedule.equipment,
                    quantity=schedule.quantity,
                    request_kind=REQUEST_KIND_WRITEOFF,
                    status=REQUEST_PENDING,
                    needed_by=schedule.next_run_on,
                    comment=full_note,
                )
                created += 1
                schedule.last_run_at = timezone.now()
                schedule.next_run_on = add_by_frequency(schedule.next_run_on, schedule.frequency)
            schedule.save(update_fields=["last_run_at", "next_run_on"])
    return created
