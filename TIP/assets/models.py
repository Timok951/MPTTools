from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from datetime import date, timedelta
import re

from core.models import Cabinet, EquipmentCategory, SoftDeleteModel, Workplace

STATUS_IN_STOCK = "in_stock"
STATUS_ASSIGNED = "assigned"
STATUS_CHECKED_OUT = "checked_out"
STATUS_REPAIR = "repair"
STATUS_RETIRED = "retired"

EQUIPMENT_STATUS_CHOICES = [
    (STATUS_IN_STOCK, "На складе"),
    (STATUS_ASSIGNED, "Закреплено"),
    (STATUS_CHECKED_OUT, "Выдано"),
    (STATUS_REPAIR, "В ремонте"),
    (STATUS_RETIRED, "Закончилось"),
]

MIN_REASONABLE_DATE = date(2000, 1, 1)
MAX_WARRANTY_FUTURE_DAYS = 3650
SERIAL_ALLOWED_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё\-_\/]+$")
MODEL_ALLOWED_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё\-_\/\s]+$")


class Equipment(SoftDeleteModel):
    name = models.CharField(max_length=200)
    inventory_number = models.CharField(max_length=100, unique=True)
    category = models.ForeignKey(EquipmentCategory, on_delete=models.SET_NULL, null=True, blank=True)
    photo = models.ImageField(upload_to="equipment/", null=True, blank=True, verbose_name="Фото")
    serial_number = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=200, blank=True)
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True)
    is_consumable = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=EQUIPMENT_STATUS_CHOICES, default=STATUS_IN_STOCK)
    quantity_total = models.PositiveIntegerField(default=1)
    quantity_available = models.PositiveIntegerField(default=1)
    low_stock_threshold = models.PositiveIntegerField(default=0)
    purchase_date = models.DateField(null=True, blank=True)
    warranty_end = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "inventory_number"]

    def __str__(self) -> str:
        return f"{self.name} ({self.inventory_number})"

    @property
    def is_low_stock(self) -> bool:
        return self.quantity_available <= self.low_stock_threshold

    def clean(self) -> None:
        if self.quantity_available > self.quantity_total:
            raise ValidationError("Доступное количество не может превышать общее количество.")
        serial = (self.serial_number or "").strip()
        if serial and not SERIAL_ALLOWED_RE.fullmatch(serial):
            raise ValidationError(
                {"serial_number": "Серийный номер может содержать только буквы, цифры и символы - _ /"}
            )
        model = (self.model or "").strip()
        if model and not MODEL_ALLOWED_RE.fullmatch(model):
            raise ValidationError({"model": "Модель может содержать только буквы, цифры, пробел и символы - _ /"})
        if self.purchase_date:
            if self.purchase_date < MIN_REASONABLE_DATE:
                raise ValidationError(
                    {"purchase_date": f"Дата покупки не может быть раньше {MIN_REASONABLE_DATE.strftime('%d.%m.%Y')}."}
                )
            if self.purchase_date > timezone.localdate():
                raise ValidationError({"purchase_date": "Дата покупки не может быть в будущем."})
        if self.warranty_end:
            if self.warranty_end < MIN_REASONABLE_DATE:
                raise ValidationError(
                    {"warranty_end": f"Дата гарантии не может быть раньше {MIN_REASONABLE_DATE.strftime('%d.%m.%Y')}."}
                )
            max_warranty = timezone.localdate() + timedelta(days=MAX_WARRANTY_FUTURE_DAYS)
            if self.warranty_end > max_warranty:
                raise ValidationError(
                    {
                        "warranty_end": (
                            f"Дата гарантии слишком далеко в будущем "
                            f"(максимум до {max_warranty.strftime('%d.%m.%Y')})."
                        )
                    }
                )
            if self.purchase_date and self.warranty_end < self.purchase_date:
                raise ValidationError({"warranty_end": "Гарантия не может заканчиваться раньше даты покупки."})


class InventoryAdjustment(SoftDeleteModel):
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE)
    delta = models.IntegerField()
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Adjustment #{self.pk} ({self.delta})"

    def clean(self) -> None:
        if not self.equipment_id:
            return
        new_total = self.equipment.quantity_total + self.delta
        new_available = self.equipment.quantity_available + self.delta
        if new_total < 0 or new_available < 0:
            raise ValidationError("Корректировка приведёт к отрицательному остатку.")


class EquipmentCheckout(SoftDeleteModel):
    equipment = models.ForeignKey(Equipment, on_delete=models.SET_NULL, null=True, blank=True)
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True)
    cabinet = models.ForeignKey(Cabinet, on_delete=models.SET_NULL, null=True, blank=True)
    related_request = models.ForeignKey(
        "operations.EquipmentRequest", on_delete=models.SET_NULL, null=True, blank=True
    )
    quantity = models.PositiveIntegerField(default=1)
    taken_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-taken_at"]

    def __str__(self) -> str:
        return f"Checkout #{self.pk}"

    @property
    def is_returned(self) -> bool:
        return self.returned_at is not None

    def clean(self) -> None:
        if self.returned_at and self.returned_at < self.taken_at:
            raise ValidationError("Время возврата не может быть раньше времени выдачи.")
        if not self.related_request:
            raise ValidationError("Для выдачи требуется одобренная заявка.")
        if self.related_request and self.related_request.status != "approved":
            raise ValidationError("Выдача возможна только по одобренной заявке.")
        if self.related_request and self.equipment and self.related_request.equipment_id != self.equipment_id:
            raise ValidationError("Оборудование должно совпадать с заявкой.")
        if self.related_request and self.quantity > self.related_request.quantity:
            raise ValidationError("Количество выдачи превышает количество в заявке.")
