from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0018_remove_standard_request_kind"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="equipmentrequest",
            index=models.Index(fields=["status", "-requested_at"], name="ops_req_status_reqat_idx"),
        ),
        migrations.AddIndex(
            model_name="equipmentrequest",
            index=models.Index(fields=["request_kind", "-requested_at"], name="ops_req_kind_reqat_idx"),
        ),
        migrations.AddIndex(
            model_name="equipmentrequest",
            index=models.Index(fields=["requester", "-requested_at"], name="ops_req_requester_reqat_idx"),
        ),
        migrations.AddIndex(
            model_name="equipmentrequest",
            index=models.Index(fields=["processed_by", "-requested_at"], name="ops_req_processor_reqat_idx"),
        ),
    ]
