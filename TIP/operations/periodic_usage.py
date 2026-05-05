"""Логика периодического списания расходников по расписанию."""

from __future__ import annotations

import calendar
from datetime import date

from django.db import transaction
from django.utils import timezone

from .models import MaterialUsage, PeriodicMaterialUsageSchedule


def add_one_calendar_month(d: date) -> date:
    m = d.month + 1
    y = d.year
    if m > 12:
        m = 1
        y += 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def process_due_periodic_schedules(*, today: date | None = None) -> int:
    """Создаёт записи списаний для всех просроченных расписаний. Возвращает число созданных строк MaterialUsage."""
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
                note = schedule.title.strip() if schedule.title else ""
                prefix = f"Периодическое списание #{schedule.pk}"
                full_note = f"{prefix}" + (f": {note}" if note else "")
                MaterialUsage.objects.create(
                    equipment=schedule.equipment,
                    workplace=schedule.workplace,
                    quantity=schedule.quantity,
                    used_by=schedule.created_by,
                    note=full_note,
                )
                created += 1
                schedule.last_run_at = timezone.now()
                schedule.next_run_on = add_one_calendar_month(schedule.next_run_on)
            schedule.save(update_fields=["last_run_at", "next_run_on"])
    return created
