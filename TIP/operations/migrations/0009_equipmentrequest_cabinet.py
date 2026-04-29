import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_alter_cabinet_options"),
        ("operations", "0008_equipmentrequestmessage_parent"),
    ]

    operations = [
        migrations.AddField(
            model_name="equipmentrequest",
            name="cabinet",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="equipment_requests",
                to="core.cabinet",
            ),
        ),
    ]
