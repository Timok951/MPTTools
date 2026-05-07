from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0015_alter_equipmentrequest_needed_by"),
    ]

    operations = [
        migrations.AlterField(
            model_name="equipmentrequest",
            name="request_kind",
            field=models.CharField(
                choices=[
                    ("sysadmin", "Сисадмин"),
                    ("builder", "Стройка"),
                    ("restock", "Пополнение"),
                    ("writeoff", "Списание"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="equipmentrequest",
            name="restock_non_consumable_action",
            field=models.CharField(
                blank=True,
                choices=[("set_in_stock", "Перевести на склад"), ("increase", "Увеличить количество")],
                default="",
                max_length=20,
            ),
        ),
    ]
