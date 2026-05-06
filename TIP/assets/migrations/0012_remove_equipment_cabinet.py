from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0011_delete_equipmentreservation"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="equipment",
            name="cabinet",
        ),
    ]
