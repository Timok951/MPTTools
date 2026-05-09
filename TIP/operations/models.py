from datetime import timedelta
import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from assets.models import Equipment
from core.models import Cabinet, SoftDeleteModel, Workplace

REQUEST_PENDING = "pending"
REQUEST_APPROVED = "approved"
REQUEST_REJECTED = "rejected"
# Legacy aliases kept for backward compatibility with older imports.
REQUEST_ISSUED = REQUEST_APPROVED
REQUEST_CLOSED = REQUEST_REJECTED


def default_needed_by_date():
    return timezone.localdate()


MAX_REQUEST_FUTURE_DAYS = 365
MAX_REQUEST_COMMENT_LENGTH = 500


REQUEST_STATUS_CHOICES = [
    (REQUEST_PENDING, "На рассмотрении"),
    (REQUEST_APPROVED, "Одобрена"),
    (REQUEST_REJECTED, "Отклонена"),
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
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="equipment_requests", verbose_name="Инициатор"
    )
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Рабочее место")
    cabinet = models.ForeignKey(
        Cabinet, on_delete=models.SET_NULL, null=True, blank=True, related_name="equipment_requests", verbose_name="Кабинет"
    )
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Оборудование")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    request_kind = models.CharField(max_length=20, choices=REQUEST_KIND_CHOICES, verbose_name="Тип заявки")
    non_consumable_target_status = models.CharField(
        max_length=20,
        choices=NON_CONSUMABLE_TARGET_STATUS_CHOICES,
        blank=True,
        default="",
        verbose_name="Целевой статус нерасходника",
    )
    restock_non_consumable_action = models.CharField(
        max_length=20,
        choices=RESTOCK_NON_CONSUMABLE_ACTION_CHOICES,
        blank=True,
        default="",
        verbose_name="Действие пополнения нерасходника",
    )
    status = models.CharField(max_length=20, choices=REQUEST_STATUS_CHOICES, default=REQUEST_PENDING, verbose_name="Статус")
    requested_at = models.DateTimeField(default=timezone.now, verbose_name="Создана")
    needed_by = models.DateField(default=default_needed_by_date, verbose_name="Нужно до")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_requests",
        verbose_name="Обработал",
    )
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name="Обработано")

    class Meta:
        ordering = ["-requested_at"]
        verbose_name = "Заявка на оборудование"
        verbose_name_plural = "Заявки на оборудование"
        indexes = [
            models.Index(fields=["status", "-requested_at"], name="ops_req_status_reqat_idx"),
            models.Index(fields=["request_kind", "-requested_at"], name="ops_req_kind_reqat_idx"),
            models.Index(fields=["requester", "-requested_at"], name="ops_req_requester_reqat_idx"),
            models.Index(fields=["processed_by", "-requested_at"], name="ops_req_processor_reqat_idx"),
        ]

    def __str__(self) -> str:
        return f"Request #{self.pk} by {self.requester}"

    def clean(self) -> None:
        if not self.equipment_id:
            raise ValidationError({"equipment": "Выберите оборудование."})
        if self.quantity < 0:
            raise ValidationError("Количество не может быть отрицательным.")
        if self.quantity == 0:
            is_set_in_stock_without_delta = (
                self.request_kind == REQUEST_KIND_RESTOCK
                and self.equipment_id
                and self.equipment is not None
                and not self.equipment.is_consumable
                and self.restock_non_consumable_action == RESTOCK_NON_CONSUMABLE_SET_IN_STOCK
            )
            if not is_set_in_stock_without_delta:
                raise ValidationError("Количество должно быть положительным.")
        if self.needed_by:
            today = timezone.localdate()
            if self.needed_by < today:
                raise ValidationError({"needed_by": "Дата «Нужно до» не может быть раньше сегодняшнего дня."})
            if self.needed_by > today + timedelta(days=MAX_REQUEST_FUTURE_DAYS):
                raise ValidationError(
                    {
                        "needed_by": (
                            f"Дата «Нужно до» слишком далеко в будущем "
                            f"(максимум +{MAX_REQUEST_FUTURE_DAYS} дней)."
                        )
                    }
                )
        comment = (self.comment or "").strip()
        if comment:
            if len(comment) > MAX_REQUEST_COMMENT_LENGTH:
                raise ValidationError(
                    {"comment": f"Комментарий слишком длинный (максимум {MAX_REQUEST_COMMENT_LENGTH} символов)."}
                )
            if not re.search(r"[0-9A-Za-zА-Яа-яЁё]", comment):
                raise ValidationError(
                    {"comment": "Комментарий должен содержать буквы или цифры, а не только символы."}
                )


class EquipmentRequestMessage(models.Model):
    request = models.ForeignKey(
        EquipmentRequest, on_delete=models.CASCADE, related_name="messages", verbose_name="Заявка"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="request_messages", verbose_name="Автор"
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
        verbose_name="Родительское сообщение",
    )
    body = models.TextField(blank=True, verbose_name="Сообщение")
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Создано")

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Сообщение в заявке"
        verbose_name_plural = "Сообщения в заявках"

    def __str__(self) -> str:
        return f"Request message #{self.pk} for request #{self.request_id}"


class EquipmentRequestThreadRead(models.Model):
    """Отметка прочтения переписки по заявке (для заявителя и назначенного обработчика)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="equipment_request_thread_reads",
        verbose_name="Пользователь",
    )
    equipment_request = models.ForeignKey(
        EquipmentRequest,
        on_delete=models.CASCADE,
        related_name="thread_reads",
        verbose_name="Заявка",
    )
    last_read_at = models.DateTimeField(default=timezone.now, verbose_name="Прочитано")

    class Meta:
        verbose_name = "Отметка прочтения треда заявки"
        verbose_name_plural = "Отметки прочтения тредов заявок"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "equipment_request"],
                name="ops_equipmentrequestthreadread_user_req_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"Thread read user={self.user_id} request={self.equipment_request_id}"


class EquipmentRequestPhoto(models.Model):
    request = models.ForeignKey(
        EquipmentRequest, on_delete=models.CASCADE, related_name="photos", verbose_name="Заявка"
    )
    message = models.ForeignKey(
        EquipmentRequestMessage,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attached_photos",
        verbose_name="Сообщение",
    )
    image = models.ImageField(upload_to="requests/", verbose_name="Изображение")
    caption = models.CharField(max_length=200, blank=True, verbose_name="Подпись")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_request_photos",
        verbose_name="Кем загружено",
    )
    uploaded_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Загружено")

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        verbose_name = "Фото заявки"
        verbose_name_plural = "Фото заявок"

    def __str__(self) -> str:
        return f"Request photo #{self.pk} for request #{self.request_id}"


class MaterialUsage(SoftDeleteModel):
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Оборудование")
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Рабочее место")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Количество")
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Кем списано"
    )
    used_at = models.DateTimeField(default=timezone.now, verbose_name="Списано")
    related_request = models.ForeignKey(
        EquipmentRequest, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Связанная заявка"
    )
    note = models.TextField(blank=True, verbose_name="Примечание")

    class Meta:
        ordering = ["-used_at"]
        verbose_name = "Списание материала"
        verbose_name_plural = "Списания материалов"

    def __str__(self) -> str:
        return f"Usage #{self.pk}"

    def clean(self) -> None:
        if self.quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")


PERIODIC_USAGE_MONTHLY = "monthly"
PERIODIC_USAGE_DAILY = "daily"
PERIODIC_USAGE_WEEKLY = "weekly"
PERIODIC_USAGE_BIWEEKLY = "biweekly"
PERIODIC_USAGE_QUARTERLY = "quarterly"
PERIODIC_USAGE_YEARLY = "yearly"

PERIODIC_USAGE_FREQUENCY_CHOICES = [
    (PERIODIC_USAGE_DAILY, "Каждый день"),
    (PERIODIC_USAGE_WEEKLY, "Раз в неделю"),
    (PERIODIC_USAGE_BIWEEKLY, "Раз в 2 недели"),
    (PERIODIC_USAGE_MONTHLY, "Раз в месяц"),
    (PERIODIC_USAGE_QUARTERLY, "Раз в квартал"),
    (PERIODIC_USAGE_YEARLY, "Раз в год"),
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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

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


