from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Cabinet, EquipmentCategory, SoftDeleteModel, Supplier, Workplace

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
    (STATUS_RETIRED, "Списано"),
]


class Equipment(SoftDeleteModel):
    name = models.CharField(max_length=200)
    inventory_number = models.CharField(max_length=100, unique=True)
    category = models.ForeignKey(EquipmentCategory, on_delete=models.SET_NULL, null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    photo = models.ImageField(upload_to="equipment/", null=True, blank=True, verbose_name="Фото")
    serial_number = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=200, blank=True)
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True)
    cabinet = models.ForeignKey(Cabinet, on_delete=models.SET_NULL, null=True, blank=True)
    is_consumable = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=EQUIPMENT_STATUS_CHOICES, default=STATUS_IN_STOCK)
    quantity_total = models.PositiveIntegerField(default=1)
    quantity_available = models.PositiveIntegerField(default=1)
    low_stock_threshold = models.PositiveIntegerField(default=0)
    purchase_date = models.DateField(null=True, blank=True)
    warranty_end = models.DateField(null=True, blank=True)
    last_inventory_at = models.DateField(null=True, blank=True)
    inventory_interval_days = models.PositiveIntegerField(default=180)
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

    @property
    def inventory_due_at(self):
        if not self.last_inventory_at:
            return None
        return self.last_inventory_at + timedelta(days=self.inventory_interval_days)

    @property
    def is_inventory_due(self) -> bool:
        due_at = self.inventory_due_at
        if not due_at:
            return True
        return due_at <= timezone.now().date()

    def clean(self) -> None:
        if self.quantity_available > self.quantity_total:
            raise ValidationError("Available quantity cannot exceed total quantity.")


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
            raise ValidationError("Adjustment would make stock negative.")


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
            raise ValidationError("Return time cannot be earlier than taken time.")
        if not self.related_request:
            raise ValidationError("Approved request is required for checkout.")
        if self.related_request and self.related_request.status != "approved":
            raise ValidationError("Checkout requires an approved request.")
        if self.related_request and self.equipment and self.related_request.equipment_id != self.equipment_id:
            raise ValidationError("Checkout equipment must match the request.")
        if self.related_request and self.quantity > self.related_request.quantity:
            raise ValidationError("Checkout quantity exceeds the request quantity.")
        if self.equipment and not self.is_returned and not self.pk:
            if self.quantity > self.equipment.quantity_available:
                raise ValidationError("Not enough available stock for checkout.")
