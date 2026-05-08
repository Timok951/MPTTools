from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class AuditLog(models.Model):
    action = models.CharField(max_length=30, verbose_name="Действие")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Исполнитель"
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, verbose_name="Тип сущности")
    object_id = models.CharField(max_length=64, verbose_name="ID объекта")
    object_repr = models.CharField(max_length=200, verbose_name="Представление объекта")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Создано")
    meta = models.JSONField(default=dict, blank=True, verbose_name="Метаданные")

    class Meta:
        ordering = ["created_at"]
        verbose_name = _("Запись аудита")
        verbose_name_plural = _("Журнал аудита")

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
        verbose_name=_("Исполнитель"),
    )
    action = models.CharField(max_length=20, verbose_name=_("Действие"))
    entity_slug = models.CharField(max_length=64, db_index=True, verbose_name=_("Сущность"))
    object_repr = models.CharField(max_length=200, blank=True, verbose_name=_("Объект"))
    path = models.CharField(max_length=500, blank=True, verbose_name=_("Путь"))
    meta = models.JSONField(default=dict, blank=True, verbose_name=_("Метаданные"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Создано"))

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("Запись журнала портала")
        verbose_name_plural = _("Журнал портала")

    def __str__(self) -> str:
        return f"{self.action} {self.entity_slug} {self.object_repr}"
