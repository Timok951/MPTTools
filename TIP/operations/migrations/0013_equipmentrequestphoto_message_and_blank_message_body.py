from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0012_rename_periodic_usage_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="equipmentrequestphoto",
            name="message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="attached_photos",
                to="operations.equipmentrequestmessage",
            ),
        ),
        migrations.AlterField(
            model_name="equipmentrequestmessage",
            name="body",
            field=models.TextField(blank=True),
        ),
    ]
