from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


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


class AdminPortalLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_portal_logs",
    )
    action = models.CharField(max_length=20)
    entity_slug = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField(max_length=200, blank=True)
    path = models.CharField(max_length=500, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Admin portal log entry")
        verbose_name_plural = _("Admin portal log")

    def __str__(self) -> str:
        return f"{self.action} {self.entity_slug} {self.object_repr}"
