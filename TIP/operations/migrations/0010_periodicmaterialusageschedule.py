# Generated manually

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("operations", "0009_equipmentrequest_cabinet"),
    ]

    operations = [
        migrations.CreateModel(
            name="PeriodicMaterialUsageSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("title", models.CharField(blank=True, max_length=200, verbose_name="Название")),
                ("quantity", models.PositiveIntegerField(default=1, verbose_name="Количество за раз")),
                (
                    "frequency",
                    models.CharField(
                        choices=[("monthly", "Раз в месяц")],
                        default="monthly",
                        max_length=20,
                        verbose_name="Периодичность",
                    ),
                ),
                ("next_run_on", models.DateField(verbose_name="Следующее списание")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активно")),
                ("last_run_at", models.DateTimeField(blank=True, null=True, verbose_name="Последний запуск")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_periodic_usage_schedules",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кем создано",
                    ),
                ),
                (
                    "equipment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="periodic_usage_schedules",
                        to="assets.equipment",
                        verbose_name="Оборудование",
                    ),
                ),
                (
                    "workplace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="core.workplace",
                        verbose_name="Рабочее место",
                    ),
                ),
            ],
            options={
                "verbose_name": "Периодическое списание",
                "verbose_name_plural": "Периодические списания",
                "ordering": ["next_run_on", "pk"],
            },
        ),
    ]
