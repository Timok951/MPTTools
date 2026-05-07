from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0013_equipmentrequestphoto_message_and_blank_message_body"),
    ]

    operations = [
        migrations.AddField(
            model_name="equipmentrequest",
            name="non_consumable_target_status",
            field=models.CharField(
                blank=True,
                choices=[("repair", "В ремонте"), ("retired", "Закончилось")],
                default="",
                max_length=20,
            ),
        ),
    ]

