from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_registrationallowedemaildomain"),
    ]

    operations = [
        migrations.AlterField(
            model_name="registrationallowedemaildomain",
            name="domain",
            field=models.CharField(
                help_text="Без символа @, например mpt.ru или subs.example.org",
                max_length=253,
                unique=True,
                verbose_name="Домен",
            ),
        ),
        migrations.AlterField(
            model_name="registrationallowedemaildomain",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="Разрешён"),
        ),
        migrations.AlterField(
            model_name="registrationallowedemaildomain",
            name="notes",
            field=models.CharField(blank=True, max_length=200, verbose_name="Заметка"),
        ),
    ]
