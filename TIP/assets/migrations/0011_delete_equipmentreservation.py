# Generated manually

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0010_equipment_reservation"),
    ]

    operations = [
        migrations.DeleteModel(name="EquipmentReservation"),
    ]
