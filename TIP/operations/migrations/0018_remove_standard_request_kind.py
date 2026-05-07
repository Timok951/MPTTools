from django.db import migrations, models


def migrate_standard_to_writeoff_forward(apps, schema_editor):
    EquipmentRequest = apps.get_model("operations", "EquipmentRequest")
    UserPreference = apps.get_model("core", "UserPreference")

    EquipmentRequest.objects.filter(request_kind="standard").update(request_kind="writeoff")
    UserPreference.objects.filter(default_request_kind="standard").update(default_request_kind="writeoff")


def migrate_standard_to_writeoff_backward(apps, schema_editor):
    EquipmentRequest = apps.get_model("operations", "EquipmentRequest")
    UserPreference = apps.get_model("core", "UserPreference")

    EquipmentRequest.objects.filter(request_kind="writeoff").update(request_kind="standard")
    UserPreference.objects.filter(default_request_kind="writeoff").update(default_request_kind="standard")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_employeeschedule"),
        ("operations", "0017_remove_legacy_request_kinds"),
    ]

    operations = [
        migrations.RunPython(migrate_standard_to_writeoff_forward, migrate_standard_to_writeoff_backward),
        migrations.AlterField(
            model_name="equipmentrequest",
            name="request_kind",
            field=models.CharField(
                choices=[
                    ("restock", "Пополнение"),
                    ("writeoff", "Списание"),
                ],
                max_length=20,
            ),
        ),
    ]
