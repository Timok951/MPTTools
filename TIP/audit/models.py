from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    object_repr = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.action} {self.object_repr}"

    @property
    def model_label(self) -> str:
        return f"{self.content_type.app_label}.{self.content_type.model}"
