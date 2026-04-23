from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0006_alter_equipment_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="equipment",
            name="photo",
            field=models.ImageField(blank=True, null=True, upload_to="equipment/", verbose_name="Фото"),
        ),
    ]
