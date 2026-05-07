from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0019_request_history_indexes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="periodicmaterialusageschedule",
            name="frequency",
            field=models.CharField(
                choices=[
                    ("daily", "Каждый день"),
                    ("weekly", "Раз в неделю"),
                    ("biweekly", "Раз в 2 недели"),
                    ("monthly", "Раз в месяц"),
                    ("quarterly", "Раз в квартал"),
                    ("yearly", "Раз в год"),
                ],
                default="monthly",
                max_length=20,
                verbose_name="Периодичность",
            ),
        ),
    ]
