from django.conf import settings
from django.core.exceptions import ValidationError
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
    deleted_at = models.DateTimeField(null=True, blank=True, verbose_name="Удалено")

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


class EquipmentCategory(SoftDeleteModel):
    name = models.CharField(max_length=200, unique=True, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")

    class Meta:
        ordering = ["name"]
        verbose_name = "Категория оборудования"
        verbose_name_plural = "Категории оборудования"

    def __str__(self) -> str:
        return self.name


class Workplace(SoftDeleteModel):
    name = models.CharField(max_length=200, unique=True, verbose_name="Название")
    location = models.CharField(max_length=200, blank=True, verbose_name="Локация")
    map_address = models.CharField(max_length=255, blank=True, verbose_name="Адрес на карте")
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Широта")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Долгота")
    description = models.TextField(blank=True, verbose_name="Описание")

    class Meta:
        ordering = ["name"]
        verbose_name = "Рабочее место"
        verbose_name_plural = "Рабочие места"

    def __str__(self) -> str:
        return self.name


class Cabinet(SoftDeleteModel):
    workplace = models.ForeignKey(
        Workplace, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Рабочее место"
    )
    code = models.CharField(max_length=50, unique=True, verbose_name="Код")
    name = models.CharField(max_length=200, verbose_name="Название")
    floor = models.CharField(max_length=50, blank=True, verbose_name="Этаж")
    description = models.TextField(blank=True, verbose_name="Описание")

    class Meta:
        ordering = ["name"]
        verbose_name = "Кабинет"
        verbose_name_plural = "Кабинеты"

    def clean(self) -> None:
        name = (self.name or "").strip()
        if not name:
            raise ValidationError("Название кабинета не может быть пустым.")
        if not name.isdigit():
            raise ValidationError("Название кабинета должно содержать только цифры (например: 101).")
        floor = (self.floor or "").strip()
        if floor and not floor.isdigit():
            raise ValidationError("Этаж должен содержать только цифры.")

    def __str__(self) -> str:
        return self.name


class WorkplaceMember(SoftDeleteModel):
    workplace = models.ForeignKey(Workplace, on_delete=models.CASCADE, verbose_name="Рабочее место")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Пользователь")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, verbose_name="Роль")
    assigned_at = models.DateTimeField(default=timezone.now, verbose_name="Назначен")
    note = models.TextField(blank=True, verbose_name="Примечание")

    class Meta:
        ordering = ["workplace__name", "user__username"]
        unique_together = [("workplace", "user")]
        verbose_name = "Участник рабочего места"
        verbose_name_plural = "Участники рабочих мест"

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
        ("active", _("Активные выдачи")),
        (CHECKOUT_FILTER_RETURNED, _("Возвращённые выдачи")),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences", verbose_name="Пользователь"
    )
    theme_variant = models.CharField(
        max_length=20, choices=THEME_CHOICES, default=THEME_DEFAULT, verbose_name="Тема интерфейса"
    )
    page_size = models.PositiveSmallIntegerField(choices=PAGE_SIZE_CHOICES, default=25, verbose_name="Размер страницы")
    preferred_language = models.CharField(max_length=10, default="ru", verbose_name="Предпочитаемый язык")
    date_display_format = models.CharField(
        max_length=20, choices=DATE_FORMAT_CHOICES, default=DATE_FORMAT_COMPACT, verbose_name="Формат даты"
    )
    default_request_status = models.CharField(
        max_length=20, blank=True, default="pending", verbose_name="Статус заявки по умолчанию"
    )
    default_request_kind = models.CharField(
        max_length=20, blank=True, default="", verbose_name="Тип заявки по умолчанию"
    )
    default_usage_period_days = models.PositiveSmallIntegerField(default=30, verbose_name="Период операций (дни)")
    default_checkout_status = models.CharField(
        max_length=20,
        choices=CHECKOUT_FILTER_CHOICES,
        blank=True,
        default=CHECKOUT_FILTER_ALL,
        verbose_name="Фильтр выдач по умолчанию",
    )
    hotkeys_enabled = models.BooleanField(default=True, verbose_name="Горячие клавиши включены")
    show_hotkey_legend = models.BooleanField(default=True, verbose_name="Показывать легенду горячих клавиш")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ["user__username"]
        verbose_name = "Пользовательская настройка"
        verbose_name_plural = "Пользовательские настройки"

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


class EmployeeSchedule(models.Model):
    SCHEDULE_5_2 = "5_2"
    SCHEDULE_2_2 = "2_2"
    SCHEDULE_CUSTOM = "custom"
    SCHEDULE_CHOICES = [
        (SCHEDULE_5_2, "5/2"),
        (SCHEDULE_2_2, "2/2"),
        (SCHEDULE_CUSTOM, "Кастомный"),
    ]
    WEEKDAY_CHOICES = [
        ("0", "Пн"),
        ("1", "Вт"),
        ("2", "Ср"),
        ("3", "Чт"),
        ("4", "Пт"),
        ("5", "Сб"),
        ("6", "Вс"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="schedule",
        verbose_name="Сотрудник",
    )
    schedule_type = models.CharField(
        max_length=16,
        choices=SCHEDULE_CHOICES,
        default=SCHEDULE_5_2,
        verbose_name="Режим графика",
    )
    cycle_start_date = models.DateField(
        default=timezone.localdate,
        help_text="Точка отсчёта для графика 2/2.",
        verbose_name="Дата начала цикла 2/2",
    )
    custom_workdays = models.CharField(
        max_length=32,
        blank=True,
        default="0,1,2,3,4",
        help_text="Для кастомного графика: номера дней недели через запятую (0=Пн ... 6=Вс).",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        ordering = ["user__username"]
        verbose_name = "График сотрудника"
        verbose_name_plural = "Графики сотрудников"

    def __str__(self) -> str:
        return f"{self.user} ({self.get_schedule_type_display()})"

class DirectMessage(models.Model):
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_direct_messages",
        verbose_name="Отправитель",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_direct_messages",
        verbose_name="Получатель",
    )
    body = models.TextField(verbose_name="Сообщение")
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Создано")
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="Прочитано")

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "Личное сообщение"
        verbose_name_plural = "Личные сообщения"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(sender=models.F("recipient")),
                name="core_directmessage_sender_not_recipient",
            )
        ]

    def __str__(self) -> str:
        return f"{self.sender} -> {self.recipient} ({self.created_at:%Y-%m-%d %H:%M})"


class PasswordResetCode(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_codes",
        verbose_name="Пользователь",
    )
    email = models.EmailField(verbose_name="Email")
    code_hash = models.CharField(max_length=256, verbose_name="Хеш кода")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Создан")
    expires_at = models.DateTimeField(verbose_name="Истекает")
    used_at = models.DateTimeField(null=True, blank=True, verbose_name="Использован")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Код сброса пароля"
        verbose_name_plural = "Коды сброса пароля"

    @property
    def is_active(self) -> bool:
        return self.used_at is None and self.expires_at >= timezone.now()

    def __str__(self) -> str:
        return f"Password reset code for {self.user} ({self.email})"


class RegistrationAllowedEmailDomain(models.Model):
    """Домены почты, с которых разрешена регистрация и сценарий восстановления пароля по коду."""

    domain = models.CharField(
        max_length=253,
        unique=True,
        verbose_name="Домен",
        help_text="Без символа @, например mpt.ru или subs.example.org",
    )
    is_active = models.BooleanField(default=True, verbose_name="Разрешён")
    notes = models.CharField(max_length=200, blank=True, verbose_name="Заметка")

    class Meta:
        ordering = ["domain"]
        verbose_name = "Разрешённый домен почты для регистрации"
        verbose_name_plural = "Разрешённые домены почты для регистрации"

    def __str__(self) -> str:
        return f"@{self.domain}" if self.domain else "(empty)"

    def clean(self) -> None:
        super().clean()
        d = (self.domain or "").strip().lower().lstrip("@")
        if not d:
            raise ValidationError({"domain": "Укажите домен."})
        if "@" in d or "/" in d or " " in d:
            raise ValidationError({"domain": "Укажите только имя домена, без @ и пути."})

    def save(self, *args, **kwargs) -> None:
        self.domain = (self.domain or "").strip().lower().lstrip("@")
        super().save(*args, **kwargs)
