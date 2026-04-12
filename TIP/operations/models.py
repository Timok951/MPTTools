from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from assets.models import Equipment
from core.models import SoftDeleteModel, Workplace

REQUEST_PENDING = "pending"
REQUEST_APPROVED = "approved"
REQUEST_REJECTED = "rejected"
REQUEST_ISSUED = "issued"
REQUEST_CLOSED = "closed"

REQUEST_STATUS_CHOICES = [
    (REQUEST_PENDING, "На рассмотрении"),
    (REQUEST_APPROVED, "Одобрена"),
    (REQUEST_REJECTED, "Отклонена"),
    (REQUEST_ISSUED, "Выдана"),
    (REQUEST_CLOSED, "Закрыта"),
]

REQUEST_KIND_SYSADMIN = "sysadmin"
REQUEST_KIND_BUILDER = "builder"

REQUEST_KIND_CHOICES = [
    (REQUEST_KIND_SYSADMIN, "Сисадмин"),
    (REQUEST_KIND_BUILDER, "Стройка"),
]


class EquipmentRequest(SoftDeleteModel):
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="equipment_requests"
    )
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True)
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    request_kind = models.CharField(max_length=20, choices=REQUEST_KIND_CHOICES)
    status = models.CharField(max_length=20, choices=REQUEST_STATUS_CHOICES, default=REQUEST_PENDING)
    requested_at = models.DateTimeField(default=timezone.now)
    needed_by = models.DateField(null=True, blank=True)
    comment = models.TextField(blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_requests",
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self) -> str:
        return f"Request #{self.pk} by {self.requester}"

    def clean(self) -> None:
        if self.equipment and self.quantity > self.equipment.quantity_available:
            raise ValidationError("Requested quantity exceeds available stock.")


class MaterialUsage(SoftDeleteModel):
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True)
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    used_at = models.DateTimeField(default=timezone.now)
    related_request = models.ForeignKey(EquipmentRequest, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-used_at"]

    def __str__(self) -> str:
        return f"Usage #{self.pk}"

    def clean(self) -> None:
        if self.equipment and self.quantity > self.equipment.quantity_available:
            raise ValidationError("Usage quantity exceeds available stock.")


class WorkTimer(SoftDeleteModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True)
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Timer #{self.pk} ({self.user})"

    @property
    def duration_seconds(self) -> int:
        if not self.ended_at:
            return 0
        return int((self.ended_at - self.started_at).total_seconds())

    def clean(self) -> None:
        if self.ended_at and self.ended_at < self.started_at:
            raise ValidationError("End time cannot be before start time.")
