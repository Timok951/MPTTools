from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_workplace_latitude_workplace_longitude_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="cabinet",
            options={"ordering": ["name"]},
        ),
    ]
