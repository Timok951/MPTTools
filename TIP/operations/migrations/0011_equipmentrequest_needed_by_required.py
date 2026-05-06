import django.utils.timezone
from django.db import migrations, models


def fill_needed_by_dates(apps, schema_editor):
    EquipmentRequest = apps.get_model("operations", "EquipmentRequest")
    today = django.utils.timezone.localdate()
    for row in EquipmentRequest.objects.filter(needed_by__isnull=True).iterator():
        row.needed_by = today
        row.save(update_fields=["needed_by"])


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0010_periodicmaterialusageschedule"),
    ]

    operations = [
        migrations.RunPython(fill_needed_by_dates, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="equipmentrequest",
            name="needed_by",
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
    ]
