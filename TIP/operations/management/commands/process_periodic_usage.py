from django.core.management.base import BaseCommand

from operations.periodic_usage import process_due_periodic_schedules


class Command(BaseCommand):
    help = (
        "Создаёт записи списания (MaterialUsage) по активным расписаниям PeriodicMaterialUsageSchedule, "
        "у которых наступила дата next_run_on. Запускайте по cron (например, ежедневно)."
    )

    def handle(self, *args, **options):
        n = process_due_periodic_schedules()
        self.stdout.write(self.style.SUCCESS(f"Создано записей списания: {n}"))
