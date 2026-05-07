from __future__ import annotations

from datetime import date

from .models import EmployeeSchedule


def is_working_day_for_user(user, target_date: date | None = None) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return True
    if getattr(user, "is_superuser", False):
        return True
    target_date = target_date or date.today()
    schedule = getattr(user, "schedule", None)
    if not schedule or not schedule.is_active:
        return True
    if schedule.schedule_type == EmployeeSchedule.SCHEDULE_5_2:
        return target_date.weekday() < 5
    if schedule.schedule_type == EmployeeSchedule.SCHEDULE_2_2:
        delta_days = (target_date - schedule.cycle_start_date).days
        return (delta_days % 4) in (0, 1)
    allowed = {part.strip() for part in (schedule.custom_workdays or "").split(",") if part.strip()}
    return str(target_date.weekday()) in allowed
