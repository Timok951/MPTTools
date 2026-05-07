from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0014_cleanup_equipment_name_numeric_suffixes_again"),
    ]

    operations = [
        migrations.AlterField(
            model_name="equipment",
            name="status",
            field=models.CharField(
                choices=[
                    ("in_stock", "На складе"),
                    ("assigned", "Закреплено"),
                    ("checked_out", "Выдано"),
                    ("repair", "В ремонте"),
                    ("retired", "Закончилось"),
                ],
                default="in_stock",
                max_length=20,
            ),
        ),
    ]

