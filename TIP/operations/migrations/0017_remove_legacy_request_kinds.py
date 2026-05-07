from django.db import migrations, models


def migrate_request_kinds_forward(apps, schema_editor):
    EquipmentRequest = apps.get_model("operations", "EquipmentRequest")
    UserPreference = apps.get_model("core", "UserPreference")

    EquipmentRequest.objects.filter(request_kind__in=["sysadmin", "builder"]).update(request_kind="standard")
    UserPreference.objects.filter(default_request_kind__in=["sysadmin", "builder"]).update(
        default_request_kind="standard"
    )


def migrate_request_kinds_backward(apps, schema_editor):
    EquipmentRequest = apps.get_model("operations", "EquipmentRequest")
    UserPreference = apps.get_model("core", "UserPreference")

    EquipmentRequest.objects.filter(request_kind="standard").update(request_kind="builder")
    UserPreference.objects.filter(default_request_kind="standard").update(default_request_kind="builder")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_employeeschedule"),
        ("operations", "0016_equipmentrequest_restock_non_consumable_action_and_request_kind"),
    ]

    operations = [
        migrations.RunPython(migrate_request_kinds_forward, migrate_request_kinds_backward),
        migrations.AlterField(
            model_name="equipmentrequest",
            name="request_kind",
            field=models.CharField(
                choices=[
                    ("standard", "Обычная"),
                    ("restock", "Пополнение"),
                    ("writeoff", "Списание"),
                ],
                max_length=20,
            ),
        ),
    ]
