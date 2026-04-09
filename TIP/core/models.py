from django.conf import settings
from django.db import models
from django.utils import timezone

ROLE_ADMIN = "admin"
ROLE_WAREHOUSE = "warehouse"
ROLE_SYSADMIN = "sysadmin"
ROLE_BUILDER = "builder"

ROLE_CHOICES = [
    (ROLE_ADMIN, "Administrator"),
    (ROLE_WAREHOUSE, "Warehouse"),
    (ROLE_SYSADMIN, "Sysadmin"),
    (ROLE_BUILDER, "Builder"),
]


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(models.Model):
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = ActiveManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        if self.deleted_at:
            return
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    def restore(self):
        if not self.deleted_at:
            return
        self.deleted_at = None
        self.save(update_fields=["deleted_at"])

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class Supplier(SoftDeleteModel):
    name = models.CharField(max_length=200, unique=True)
    contact_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class EquipmentCategory(SoftDeleteModel):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "equipment categories"

    def __str__(self) -> str:
        return self.name


class Workplace(SoftDeleteModel):
    name = models.CharField(max_length=200, unique=True)
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Cabinet(SoftDeleteModel):
    workplace = models.ForeignKey(Workplace, on_delete=models.SET_NULL, null=True, blank=True)
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    floor = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class WorkplaceMember(SoftDeleteModel):
    workplace = models.ForeignKey(Workplace, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    assigned_at = models.DateTimeField(default=timezone.now)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["workplace__name", "user__username"]
        unique_together = [("workplace", "user")]

    def __str__(self) -> str:
        return f"{self.workplace} - {self.user} ({self.role})"
