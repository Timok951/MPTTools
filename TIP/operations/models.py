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


def default_needed_by_date():
    return timezone.localdate()


REQUEST_STATUS_CHOICES = [
    (REQUEST_PENDING, "На рассмотрении"),
    (REQUEST_APPROVED, "Одобрена"),
    (REQUEST_REJECTED, "Отклонена"),
    (REQUEST_ISSUED, "Выдана"),
    (REQUEST_CLOSED, "Закрыта"),
]

REQUEST_KIND_RESTOCK = "restock"
REQUEST_KIND_WRITEOFF = "writeoff"

# Backward-compatible aliases for legacy imports.
REQUEST_KIND_STANDARD = REQUEST_KIND_WRITEOFF
REQUEST_KIND_SYSADMIN = REQUEST_KIND_WRITEOFF
REQUEST_KIND_BUILDER = REQUEST_KIND_WRITEOFF

REQUEST_KIND_CHOICES = [
    (REQUEST_KIND_RESTOCK, "Пополнение"),
    (REQUEST_KIND_WRITEOFF, "Списание"),
]

NON_CONSUMABLE_TARGET_REPAIR = "repair"
NON_CONSUMABLE_TARGET_RETIRED = "retired"
NON_CONSUMABLE_TARGET_STATUS_CHOICES = [
    (NON_CONSUMABLE_TARGET_REPAIR, "В ремонте"),
    (NON_CONSUMABLE_TARGET_RETIRED, "Закончилось"),
]

RESTOCK_NON_CONSUMABLE_SET_IN_STOCK = "set_in_stock"
RESTOCK_NON_CONSUMABLE_INCREASE = "increase"
RESTOCK_NON_CONSUMABLE_ACTION_CHOICES = [
    (RESTOCK_NON_CONSUMABLE_SET_IN_STOCK, "Перевести на склад"),
    (RESTOCK_NON_CONSUMABLE_INCREASE, "Увеличить количество"),
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
    non_consumable_target_status = models.CharField(
        max_length=20,
        choices=NON_CONSUMABLE_TARGET_STATUS_CHOICES,
        blank=True,
        default="",
    )
    restock_non_consumable_action = models.CharField(
        max_length=20,
        choices=RESTOCK_NON_CONSUMABLE_ACTION_CHOICES,
        blank=True,
        default="",
    )
    status = models.CharField(max_length=20, choices=REQUEST_STATUS_CHOICES, default=REQUEST_PENDING)
    requested_at = models.DateTimeField(default=timezone.now)
    needed_by = models.DateField(default=default_needed_by_date)
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
        indexes = [
            models.Index(fields=["status", "-requested_at"], name="ops_req_status_reqat_idx"),
            models.Index(fields=["request_kind", "-requested_at"], name="ops_req_kind_reqat_idx"),
            models.Index(fields=["requester", "-requested_at"], name="ops_req_requester_reqat_idx"),
            models.Index(fields=["processed_by", "-requested_at"], name="ops_req_processor_reqat_idx"),
        ]

    def __str__(self) -> str:
        return f"Request #{self.pk} by {self.requester}"

    def clean(self) -> None:
        if self.quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")


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
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"Request message #{self.pk} for request #{self.request_id}"


class EquipmentRequestPhoto(models.Model):
    request = models.ForeignKey(EquipmentRequest, on_delete=models.CASCADE, related_name="photos")
    message = models.ForeignKey(
        EquipmentRequestMessage,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attached_photos",
    )
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
        if self.quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")


PERIODIC_USAGE_MONTHLY = "monthly"

PERIODIC_USAGE_FREQUENCY_CHOICES = [
    (PERIODIC_USAGE_MONTHLY, "Раз в месяц"),
]


class PeriodicMaterialUsageSchedule(SoftDeleteModel):
    """Автоматическое уменьшение остатка расходника по календарю (запись MaterialUsage) — команда process_periodic_usage."""

    title = models.CharField(max_length=200, blank=True, verbose_name="Название")
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        related_name="periodic_usage_schedules",
        verbose_name="Оборудование",
    )
    workplace = models.ForeignKey(
        Workplace,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Рабочее место",
    )
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество за раз")
    frequency = models.CharField(
        max_length=20,
        choices=PERIODIC_USAGE_FREQUENCY_CHOICES,
        default=PERIODIC_USAGE_MONTHLY,
        verbose_name="Периодичность",
    )
    next_run_on = models.DateField(verbose_name="Следующий запуск по расписанию")
    is_active = models.BooleanField(default=True, verbose_name="Активно")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_periodic_usage_schedules",
        verbose_name="Кем создано",
    )
    last_run_at = models.DateTimeField(null=True, blank=True, verbose_name="Последний запуск")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["next_run_on", "pk"]
        verbose_name = "Периодический расход по расписанию"
        verbose_name_plural = "Расписания периодического расхода"

    def __str__(self) -> str:
        return self.title.strip() or f"Расписание #{self.pk} ({self.equipment})"

    def clean(self) -> None:
        if self.quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")
        eq = self.equipment
        if eq is not None and not eq.is_consumable:
            raise ValidationError({"equipment": "Расписание доступно только для позиций с флагом «расходник»."})


