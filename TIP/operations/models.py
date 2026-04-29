from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from assets.models import Equipment
from core.models import Cabinet, SoftDeleteModel, Workplace

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
    cabinet = models.ForeignKey(Cabinet, on_delete=models.SET_NULL, null=True, blank=True, related_name="equipment_requests")
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


class EquipmentRequestMessage(models.Model):
    request = models.ForeignKey(EquipmentRequest, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="request_messages")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"Request message #{self.pk} for request #{self.request_id}"


class EquipmentRequestPhoto(models.Model):
    request = models.ForeignKey(EquipmentRequest, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="requests/")
    caption = models.CharField(max_length=200, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_request_photos",
    )
    uploaded_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self) -> str:
        return f"Request photo #{self.pk} for request #{self.request_id}"


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


