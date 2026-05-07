from django.db import migrations
import re


def cleanup_suffix_numbers_strict(apps, schema_editor):
    Equipment = apps.get_model("assets", "Equipment")
    # Удаляем любое количество хвостов вида " (123)" в конце названия.
    suffix_re = re.compile(r"(?:\s*\(\d+\))+$")
    for item in Equipment.objects.all().only("id", "name"):
        original = (item.name or "").strip()
        cleaned = suffix_re.sub("", original).strip()
        if cleaned and cleaned != original:
            Equipment.objects.filter(pk=item.pk).update(name=cleaned)


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0013_cleanup_equipment_name_suffix_numbers"),
    ]

    operations = [
        migrations.RunPython(cleanup_suffix_numbers_strict, migrations.RunPython.noop),
    ]

