from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_registrationallowedemaildomain_verbose_names"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployeeSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("schedule_type", models.CharField(choices=[("5_2", "5/2"), ("2_2", "2/2"), ("custom", "Кастомный")], default="5_2", max_length=16)),
                ("cycle_start_date", models.DateField(default=django.utils.timezone.localdate, help_text="Точка отсчёта для графика 2/2.")),
                (
                    "custom_workdays",
                    models.CharField(
                        blank=True,
                        default="0,1,2,3,4",
                        help_text="Для кастомного графика: номера дней недели через запятую (0=Пн ... 6=Вс).",
                        max_length=32,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="schedule", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "verbose_name": "График сотрудника",
                "verbose_name_plural": "Графики сотрудников",
                "ordering": ["user__username"],
            },
        ),
    ]
