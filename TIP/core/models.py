from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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


class UserPreference(models.Model):
    THEME_DEFAULT = "default"
    THEME_CONTRAST = "contrast"
    THEME_DARK = "dark"
    THEME_CHOICES = [
        (THEME_DEFAULT, _("Мягкая светлая")),
        (THEME_CONTRAST, _("Контрастная")),
        (THEME_DARK, _("Тёмная")),
    ]

    DATE_FORMAT_COMPACT = "compact"
    DATE_FORMAT_ISO = "iso"
    DATE_FORMAT_VERBOSE = "verbose"
    DATE_FORMAT_CHOICES = [
        (DATE_FORMAT_COMPACT, _("ДД.ММ.ГГГГ ЧЧ:ММ")),
        (DATE_FORMAT_ISO, _("ГГГГ-ММ-ДД ЧЧ:ММ")),
        (DATE_FORMAT_VERBOSE, _("Развёрнутый локальный формат")),
    ]

    TIMER_FILTER_ALL = ""
    TIMER_FILTER_ACTIVE = "active"
    TIMER_FILTER_FINISHED = "finished"
    TIMER_FILTER_CHOICES = [
        (TIMER_FILTER_ALL, _("Все таймеры")),
        (TIMER_FILTER_ACTIVE, _("Активные таймеры")),
        (TIMER_FILTER_FINISHED, _("Завершённые таймеры")),
    ]

    PAGE_SIZE_CHOICES = [
        (10, "10"),
        (25, "25"),
        (50, "50"),
        (100, "100"),
    ]
    CHECKOUT_FILTER_ALL = ""
    CHECKOUT_FILTER_RETURNED = "returned"
    CHECKOUT_FILTER_CHOICES = [
        (CHECKOUT_FILTER_ALL, _("Все выдачи")),
        (TIMER_FILTER_ACTIVE, _("Активные выдачи")),
        (CHECKOUT_FILTER_RETURNED, _("Возвращённые выдачи")),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences")
    theme_variant = models.CharField(max_length=20, choices=THEME_CHOICES, default=THEME_DEFAULT)
    page_size = models.PositiveSmallIntegerField(choices=PAGE_SIZE_CHOICES, default=25)
    preferred_language = models.CharField(max_length=10, default="ru")
    date_display_format = models.CharField(max_length=20, choices=DATE_FORMAT_CHOICES, default=DATE_FORMAT_COMPACT)
    default_timer_status = models.CharField(max_length=20, choices=TIMER_FILTER_CHOICES, blank=True, default=TIMER_FILTER_ALL)
    default_request_status = models.CharField(max_length=20, blank=True, default="")
    default_request_kind = models.CharField(max_length=20, blank=True, default="")
    default_usage_period_days = models.PositiveSmallIntegerField(default=30)
    default_checkout_status = models.CharField(max_length=20, choices=CHECKOUT_FILTER_CHOICES, blank=True, default=CHECKOUT_FILTER_ALL)
    hotkeys_enabled = models.BooleanField(default=True)
    show_hotkey_legend = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]
        verbose_name = "User preference"
        verbose_name_plural = "User preferences"

    def __str__(self) -> str:
        return f"Preferences for {self.user}"

    @property
    def datetime_format(self) -> str:
        return {
            self.DATE_FORMAT_COMPACT: "d.m.Y H:i",
            self.DATE_FORMAT_ISO: "Y-m-d H:i",
            self.DATE_FORMAT_VERBOSE: "j E Y, H:i",
        }.get(self.date_display_format, "d.m.Y H:i")

    @property
    def date_format(self) -> str:
        return {
            self.DATE_FORMAT_COMPACT: "d.m.Y",
            self.DATE_FORMAT_ISO: "Y-m-d",
            self.DATE_FORMAT_VERBOSE: "j E Y",
        }.get(self.date_display_format, "d.m.Y")
