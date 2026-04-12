from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_userpreference_preferred_language"),
    ]

    operations = [
        migrations.AlterField(
            model_name="userpreference",
            name="theme_variant",
            field=models.CharField(
                choices=[("default", "Soft light"), ("contrast", "High contrast"), ("dark", "Dark night")],
                default="default",
                max_length=20,
            ),
        ),
    ]
