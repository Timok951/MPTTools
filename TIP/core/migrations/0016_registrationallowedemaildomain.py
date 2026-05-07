from django.db import migrations, models


def seed_default_domain(apps, schema_editor):
    RegistrationAllowedEmailDomain = apps.get_model("core", "RegistrationAllowedEmailDomain")
    RegistrationAllowedEmailDomain.objects.get_or_create(
        domain="mpt.ru",
        defaults={"is_active": True, "notes": "По умолчанию при обновлении"},
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_userpreference_default_request_pending"),
    ]

    operations = [
        migrations.CreateModel(
            name="RegistrationAllowedEmailDomain",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("domain", models.CharField(help_text="Без символа @, например mpt.ru или subs.example.org", max_length=253, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.CharField(blank=True, max_length=200)),
            ],
            options={
                "verbose_name": "Разрешённый домен почты для регистрации",
                "verbose_name_plural": "Разрешённые домены почты для регистрации",
                "ordering": ["domain"],
            },
        ),
        migrations.RunPython(seed_default_domain, noop_reverse),
    ]
