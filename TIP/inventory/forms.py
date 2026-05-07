from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
import re
from datetime import timedelta

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from django.contrib.auth.models import User

from core.registration_domains import (
    get_registration_email_domains,
    registration_email_placeholder,
    validate_corporate_registration_email,
)
from core.models import DirectMessage, UserPreference, Workplace
from operations.models import (
    EquipmentRequest,
    EquipmentRequestMessage,
    EquipmentRequestPhoto,
    NON_CONSUMABLE_TARGET_REPAIR,
    NON_CONSUMABLE_TARGET_RETIRED,
    REQUEST_KIND_RESTOCK,
    REQUEST_KIND_WRITEOFF,
    RESTOCK_NON_CONSUMABLE_ACTION_CHOICES,
    RESTOCK_NON_CONSUMABLE_INCREASE,
    RESTOCK_NON_CONSUMABLE_SET_IN_STOCK,
    REQUEST_APPROVED,
)

MAX_ALLOWED_QUANTITY = 1000
MAX_ALLOWED_ADJUSTMENT_DELTA = 1000
MAX_REQUEST_FUTURE_DAYS = 365
MAX_REQUEST_COMMENT_LENGTH = 500


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _validate_corporate_email(email: str) -> str:
    return validate_corporate_registration_email(email)


def _lang_label(ru_text: str, en_text: str, language_code: str) -> str:
    return en_text if str(language_code).lower().startswith("en") else ru_text


def _has_meaningful_chars(value: str) -> bool:
    return bool(re.search(r"[0-9A-Za-zА-Яа-яЁё]", value or ""))


USERNAME_ALLOWED_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё._-]+$")


def _has_excessive_repetition(value: str, *, max_same_in_row: int = 4) -> bool:
    if not value:
        return False
    return bool(re.search(rf"(.)\1{{{max_same_in_row},}}", value))


def _validate_meaningful_text(value: str | None, *, field_label: str, required: bool = False) -> str:
    text = (value or "").strip()
    if not text:
        if required:
            raise ValidationError(f"Поле «{field_label}» не может состоять только из пробелов.")
        return ""
    if not _has_meaningful_chars(text):
        raise ValidationError(f"Поле «{field_label}» должно содержать буквы или цифры, а не только символы.")
    return text


class RussianAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label="Имя пользователя", widget=forms.TextInput(attrs={"autofocus": True}))
    password = forms.CharField(
        label="Пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )


class RussianUserCreationForm(UserCreationForm):
    username = forms.CharField(label="Имя пользователя", help_text="Обязательно. Не более 150 символов.")
    email = forms.EmailField(
        label="Электронная почта",
        required=True,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label="Пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    password2 = forms.CharField(
        label="Подтверждение пароля",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Введите пароль ещё раз для проверки.",
    )
    class Meta(UserCreationForm.Meta):
        fields = (*UserCreationForm.Meta.fields, "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        domains = get_registration_email_domains()
        if domains:
            self.fields["email"].help_text = "Обязательно. Выберите адрес на разрешённом домене."
        else:
            self.fields["email"].help_text = (
                "Регистрация недоступна: администратор не включил ни одного разрешённого домена почты."
            )
        self.fields["email"].widget.attrs.setdefault("placeholder", registration_email_placeholder())

    def clean_email(self):
        email = _normalize_email(self.cleaned_data.get("email"))
        _validate_corporate_email(email)
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Пользователь с таким адресом почты уже зарегистрирован.")
        return email

    def clean_username(self):
        value = (self.cleaned_data.get("username") or "").strip()
        if not value:
            raise ValidationError("Имя пользователя не может быть пустым.")
        if not USERNAME_ALLOWED_RE.fullmatch(value):
            raise ValidationError("Имя пользователя: допустимы буквы, цифры, а также . _ -")
        if _has_excessive_repetition(value):
            raise ValidationError("Имя пользователя содержит слишком много одинаковых символов подряд.")
        return value

class BackupImportForm(forms.Form):
    backup_file = forms.FileField(label=_("JSON резервная копия"))

    def clean_backup_file(self):
        backup_file = self.cleaned_data["backup_file"]
        if not backup_file.name.lower().endswith(".json"):
            raise ValidationError(_("Загрузите файл резервной копии в формате JSON."))
        return backup_file


class PostgresqlDumpImportForm(forms.Form):
    dump_file = forms.FileField(label=_("Дамп PostgreSQL (.dump, custom format)"))

    def clean_dump_file(self):
        f = self.cleaned_data["dump_file"]
        name = (f.name or "").lower()
        if not name.endswith(".dump"):
            raise ValidationError(_("Загрузите файл .dump (pg_dump -Fc)."))
        return f


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={"autocomplete": "email"},
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.setdefault("placeholder", registration_email_placeholder())
        self._request_user = user
        if user and getattr(user, "is_authenticated", False) and (getattr(user, "email", None) or "").strip():
            self.fields["email"].widget.attrs["readonly"] = True

    def clean_email(self):
        email = _normalize_email(self.cleaned_data.get("email"))
        user = self._request_user
        if user and getattr(user, "is_authenticated", False):
            profile_email = (getattr(user, "email", None) or "").strip().lower()
            if profile_email:
                if email != profile_email:
                    raise ValidationError("Введите email, указанный в вашем профиле.")
                return email
            _validate_corporate_email(email)
            if User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                raise ValidationError("Этот email уже используется другой учётной записью.")
            return email
        _validate_corporate_email(email)
        return email


class PasswordResetConfirmForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={"autocomplete": "email"},
        ),
    )
    code = forms.CharField(
        label="Код из письма",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "placeholder": "123456"}),
    )
    new_password1 = forms.CharField(
        label="Новый пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Подтверждение нового пароля",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_user = user
        self.fields["email"].widget.attrs.setdefault("placeholder", registration_email_placeholder())
        if user and getattr(user, "is_authenticated", False) and (getattr(user, "email", None) or "").strip():
            self.fields["email"].widget.attrs["readonly"] = True

    def clean_email(self):
        email = _normalize_email(self.cleaned_data.get("email"))
        user = self._request_user
        if user and getattr(user, "is_authenticated", False):
            profile_email = (getattr(user, "email", None) or "").strip().lower()
            if profile_email:
                if email != profile_email:
                    raise ValidationError("Введите email, указанный в вашем профиле.")
                return email
        _validate_corporate_email(email)
        return email

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip()

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")
        if password1 and password2 and password1 != password2:
            self.add_error("new_password2", "Пароли не совпадают.")
        if password1:
            validate_password(password1)
        return cleaned


class DirectMessageForm(forms.ModelForm):
    class Meta:
        model = DirectMessage
        fields = ["recipient", "body"]
        labels = {
            "recipient": "Пользователь",
            "body": "Сообщение",
        }
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4, "placeholder": "Напишите сообщение пользователю."}),
        }

    def __init__(self, *args, sender=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sender = sender
        self.fields["recipient"].queryset = User.objects.filter(is_active=True).exclude(pk=getattr(sender, "pk", None)).order_by("username")
        self.fields["recipient"].empty_label = "Выберите пользователя"
        self.fields["recipient"].label = "Пользователь"
        self.fields["body"].label = "Сообщение"

    def clean_recipient(self):
        recipient = self.cleaned_data["recipient"]
        if self.sender and recipient.pk == self.sender.pk:
            raise ValidationError("Нельзя отправить сообщение самому себе.")
        return recipient

    def clean_body(self):
        return _validate_meaningful_text(self.cleaned_data.get("body"), field_label="Сообщение", required=True)


class UserPreferenceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "ru")
        super().__init__(*args, **kwargs)
        t = lambda ru_text, en_text: _lang_label(ru_text, en_text, language_code)

        pref_user = getattr(self.instance, "user", None)
        self.fields["email"] = forms.EmailField(
            label=t("Электронная почта", "Email"),
            required=False,
            help_text=t(
                "Нужна для восстановления пароля по коду и связи с учётной записью.",
                "Used for password recovery by code and account contact.",
            ),
            widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "user@example.com"}),
        )
        if pref_user:
            self.fields["email"].initial = (pref_user.email or "").strip()

        self.fields["theme_variant"].label = t("Тема", "Theme")
        self.fields["theme_variant"].help_text = t(
            "Светлая, контрастная или тёмная тема интерфейса.",
            "Light, contrast, or dark interface theme.",
        )
        self.fields["theme_variant"].choices = [
            ("default", t("Мягкая светлая", "Soft light")),
            ("contrast", t("Контрастная", "Contrast")),
            ("dark", t("Тёмная", "Dark")),
        ]
        self.fields["preferred_language"] = forms.ChoiceField(
            label=t("Язык интерфейса", "Interface language"),
            choices=[("ru", t("Русский", "Russian")), ("en", t("Английский", "English"))],
            initial=self.instance.preferred_language if self.instance and self.instance.pk else "ru",
        )
        self.fields["page_size"].label = t("Размер страницы", "Page size")
        self.fields["date_display_format"].label = t("Формат даты", "Date format")
        self.fields["date_display_format"].choices = [
            ("compact", t("ДД.ММ.ГГГГ ЧЧ:ММ", "DD.MM.YYYY HH:MM")),
            ("iso", t("ГГГГ-ММ-ДД ЧЧ:ММ", "YYYY-MM-DD HH:MM")),
            ("verbose", t("Развёрнутый локальный формат", "Verbose local format")),
        ]
        self.fields["hotkeys_enabled"].label = t("Включить горячие клавиши", "Enable hotkeys")
        self.fields["show_hotkey_legend"].label = t("Показывать подсказку по горячим клавишам", "Show hotkey legend")
        self.fields["default_request_status"] = forms.ChoiceField(
            label=t("Статус заявок по умолчанию", "Default request status"),
            required=False,
            choices=[
                ("", t("Все заявки", "All requests")),
                ("pending", t("На рассмотрении", "Pending")),
                ("approved", t("Одобрена", "Approved")),
                ("rejected", t("Отклонена", "Rejected")),
            ],
            initial=self.instance.default_request_status if self.instance and self.instance.pk else "pending",
        )
        self.fields["default_request_kind"] = forms.ChoiceField(
            label=t("Тип заявок по умолчанию", "Default request type"),
            required=False,
            choices=[("", t("Все типы заявок", "All request types")), ("sysadmin", t("Сисадмин", "Sysadmin")), ("builder", t("Стройка", "Builder"))],
            initial=self.instance.default_request_kind if self.instance and self.instance.pk else "",
        )
        self.fields["default_usage_period_days"].label = t(
            "Период истории расхода материалов по умолчанию",
            "Default material usage history period",
        )
        self.fields["default_usage_period_days"].help_text = t(
            "Автоматически подставлять период журнала расхода от текущей даты (если раздел включён).",
            "Automatically prefill the usage history window from today (when that section is enabled).",
        )
        self.fields["hotkeys_enabled"].help_text = t(
            "Включить глобальные горячие клавиши вне полей формы.",
            "Enable global hotkeys outside form fields.",
        )
        self.fields["show_hotkey_legend"].help_text = t(
            "Показывать в интерфейсе подсказку по горячим клавишам.",
            "Show hotkey hints in the interface.",
        )

    def clean_email(self):
        raw = (self.cleaned_data.get("email") or "").strip()
        if not raw:
            return ""
        email = raw.lower()
        user = getattr(self.instance, "user", None)
        if user and User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
            raise ValidationError("Этот адрес уже привязан к другому пользователю.")
        return email

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit and "email" in self.cleaned_data:
            email = self.cleaned_data["email"]
            u = instance.user
            if (u.email or "").strip().lower() != (email or ""):
                u.email = email
                u.save(update_fields=["email"])
        return instance

    class Meta:
        model = UserPreference
        fields = [
            "theme_variant",
            "preferred_language",
            "page_size",
            "date_display_format",
            "default_request_status",
            "default_request_kind",
            "default_usage_period_days",
            "hotkeys_enabled",
            "show_hotkey_legend",
        ]


class EquipmentRequestForm(forms.ModelForm):
    initial_photo = forms.ImageField(
        required=False,
        label="Фото к заявке",
        help_text="Необязательно: снимок проблемы, этикетки или комплектации.",
    )
    non_consumable_target_status = forms.ChoiceField(
        required=False,
        label="Статус для нерасходуемого оборудования",
        choices=(
            (NON_CONSUMABLE_TARGET_REPAIR, "В ремонте"),
            (NON_CONSUMABLE_TARGET_RETIRED, "Закончилось"),
        ),
        help_text="Применяется только к нерасходуемым позициям.",
        initial=NON_CONSUMABLE_TARGET_REPAIR,
    )
    restock_non_consumable_action = forms.ChoiceField(
        required=False,
        label="Пополнение нерасходника",
        choices=RESTOCK_NON_CONSUMABLE_ACTION_CHOICES,
        initial=RESTOCK_NON_CONSUMABLE_INCREASE,
        help_text="Для типа заявки «Пополнение»: вернуть на склад или увеличить количество.",
    )

    class Meta:
        model = EquipmentRequest
        fields = ["workplace", "cabinet", "equipment", "quantity", "request_kind", "needed_by", "comment"]
        widgets = {
            "needed_by": forms.DateInput(attrs={"type": "date"}),
            "comment": forms.Textarea(attrs={"rows": 4, "placeholder": "Опишите, что требуется и почему."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        selected_kind = ""
        if self.is_bound:
            selected_kind = (self.data.get(self.add_prefix("request_kind")) or "").strip()
        elif self.initial.get("request_kind"):
            selected_kind = str(self.initial.get("request_kind")).strip()
        elif getattr(self.instance, "request_kind", None):
            selected_kind = str(self.instance.request_kind).strip()
        self.fields["workplace"].label = "Рабочее место"
        self.fields["workplace"].empty_label = "Выберите рабочее место"
        self.fields["cabinet"].label = "Кабинет"
        self.fields["cabinet"].empty_label = "Выберите кабинет (необязательно)"
        self.fields["request_kind"].label = "Тип заявки"
        self.fields["equipment"].label = "Оборудование"
        equipment_qs = Equipment.objects.select_related("workplace")
        if selected_kind == REQUEST_KIND_RESTOCK:
            equipment_qs = equipment_qs.filter(deleted_at__isnull=True)
        elif selected_kind == REQUEST_KIND_WRITEOFF:
            equipment_qs = equipment_qs.filter(
                Q(is_consumable=False, quantity_available__gt=0)
                | Q(is_consumable=True, quantity_total__gt=0)
            )
        else:
            equipment_qs = equipment_qs.filter(
                Q(is_consumable=False, quantity_available__gt=0, status__in=("in_stock", "repair"))
                | Q(is_consumable=True, quantity_total__gt=0, status__in=("in_stock", "repair"))
            )
        self.fields["equipment"].queryset = equipment_qs.order_by("name", "inventory_number")
        self.fields["equipment"].label_from_instance = self._equipment_label
        self.fields["equipment"].empty_label = "Выберите оборудование"
        self.fields["quantity"].label = "Количество"
        self.fields["quantity"].help_text = "Укажите нужное количество для склада."
        self.fields["needed_by"].label = "Нужно до"
        self.fields["needed_by"].required = True
        self.fields["needed_by"].help_text = "Обязательная дата, к которой желательно получить материал."
        self.fields["needed_by"].widget.format = "%Y-%m-%d"
        self.fields["needed_by"].localize = False
        if not self.is_bound and not self.initial.get("needed_by") and not (self.instance and self.instance.pk):
            self.initial["needed_by"] = today
        needed_by_value = self.initial.get("needed_by") or today
        if hasattr(needed_by_value, "strftime"):
            needed_by_value = needed_by_value.strftime("%Y-%m-%d")
        self.fields["needed_by"].initial = needed_by_value
        self.fields["comment"].label = "Комментарий"
        self.fields["comment"].help_text = "Добавьте детали, которые помогут быстрее согласовать заявку."

    @staticmethod
    def _equipment_label(item: Equipment) -> str:
        return f"{item.name} | доступно: {item.quantity_available} из {item.quantity_total}"

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity < 0:
            raise ValidationError("Количество не может быть отрицательным.")
        if quantity > MAX_ALLOWED_QUANTITY:
            raise ValidationError(f"Количество не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return quantity

    def clean_equipment(self):
        equipment = self.cleaned_data.get("equipment")
        request_kind = (self.cleaned_data.get("request_kind") or "").strip()
        if equipment:
            if request_kind == REQUEST_KIND_RESTOCK:
                return equipment
            if request_kind == REQUEST_KIND_WRITEOFF:
                allowed_qty = equipment.quantity_total if equipment.is_consumable else equipment.quantity_available
                if allowed_qty <= 0:
                    raise ValidationError("Нельзя списать позицию с нулевым количеством.")
                return equipment
            if equipment.status == "retired":
                raise ValidationError("Оборудование со статусом «Закончилось» недоступно для новых заявок.")
            allowed_qty = equipment.quantity_total if equipment.is_consumable else equipment.quantity_available
            if allowed_qty <= 0:
                raise ValidationError("Эта позиция сейчас недоступна на складе. Выберите другое оборудование.")
        return equipment

    def clean_comment(self):
        value = _validate_meaningful_text(self.cleaned_data.get("comment"), field_label="Комментарий")
        if value and len(value) > MAX_REQUEST_COMMENT_LENGTH:
            raise ValidationError(f"Комментарий слишком длинный (максимум {MAX_REQUEST_COMMENT_LENGTH} символов).")
        return value

    def clean_needed_by(self):
        # Если поле оставили пустым, считаем, что выбран текущий день.
        today = timezone.localdate()
        needed_by = self.cleaned_data.get("needed_by") or today
        if needed_by < today:
            raise ValidationError("Дата «Нужно до» не может быть раньше сегодняшнего дня.")
        if needed_by > today + timedelta(days=MAX_REQUEST_FUTURE_DAYS):
            raise ValidationError(f"Дата «Нужно до» слишком далеко в будущем (максимум +{MAX_REQUEST_FUTURE_DAYS} дней).")
        return needed_by

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        request_kind = (cleaned.get("request_kind") or "").strip()
        if equipment:
            if request_kind == REQUEST_KIND_RESTOCK:
                action = (cleaned.get("restock_non_consumable_action") or "").strip()
                allow_zero = bool(
                    quantity == 0
                    and equipment is not None
                    and not equipment.is_consumable
                    and action == RESTOCK_NON_CONSUMABLE_SET_IN_STOCK
                )
                if quantity < 0 or (quantity == 0 and not allow_zero):
                    self.add_error("quantity", "Для пополнения укажите количество больше нуля.")
                return cleaned
            if quantity <= 0:
                self.add_error("quantity", "Количество должно быть положительным.")
            allowed_qty = equipment.quantity_total if equipment.is_consumable else equipment.quantity_available
            if quantity > allowed_qty:
                self.add_error("quantity", f"Доступно только {allowed_qty} шт. по выбранной позиции.")
            if request_kind == REQUEST_KIND_WRITEOFF and allowed_qty <= 0:
                self.add_error("equipment", "Для списания выберите позицию с доступным количеством.")
        if request_kind != REQUEST_KIND_RESTOCK:
            cleaned["restock_non_consumable_action"] = ""
        if request_kind != REQUEST_KIND_WRITEOFF and equipment and not equipment.is_consumable:
            target = (cleaned.get("non_consumable_target_status") or "").strip()
            if target not in {NON_CONSUMABLE_TARGET_REPAIR, NON_CONSUMABLE_TARGET_RETIRED}:
                self.add_error("non_consumable_target_status", "Выберите статус для нерасходуемого оборудования.")
        if request_kind == REQUEST_KIND_RESTOCK:
            cleaned["non_consumable_target_status"] = ""
        return cleaned


class EquipmentRequestMessageForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequestMessage
        fields = ["body"]
        labels = {
            "body": "Сообщение",
        }
        widgets = {
            "body": forms.Textarea(attrs={"rows": 3, "placeholder": "Добавьте сообщение по заявке."}),
        }

    def clean_body(self):
        return _validate_meaningful_text(self.cleaned_data.get("body"), field_label="Сообщение", required=True)


class EquipmentRequestPhotoForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequestPhoto
        fields = ["image", "caption"]
        widgets = {
            "caption": forms.TextInput(attrs={"placeholder": "Подпись к фото (необязательно)."}),
        }

    def clean_caption(self):
        return _validate_meaningful_text(self.cleaned_data.get("caption"), field_label="Подпись к фото")


class InventoryAdjustmentForm(forms.ModelForm):
    class Meta:
        model = InventoryAdjustment
        fields = ["equipment", "delta", "reason"]
        labels = {
            "equipment": "Оборудование",
            "delta": "Изменение остатка",
            "reason": "Причина",
        }
        widgets = {
            "reason": forms.TextInput(attrs={"placeholder": "Почему требуется корректировка остатка?"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].empty_label = "Выберите оборудование"
        self.fields["delta"].help_text = "Используйте положительные числа для пополнения склада и отрицательные для уменьшения остатка."
        self.fields["reason"].help_text = "Этот текст отображается в истории инвентаря и журналах аудита."

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        delta = cleaned.get("delta")
        if delta is not None and abs(delta) > MAX_ALLOWED_ADJUSTMENT_DELTA:
            self.add_error("delta", f"Изменение не должно превышать {MAX_ALLOWED_ADJUSTMENT_DELTA} по модулю.")
        if equipment and delta is not None:
            new_total = equipment.quantity_total + delta
            new_available = equipment.quantity_available + delta
            if new_total < 0 or new_available < 0:
                raise ValidationError("Корректировка приведёт к отрицательному остатку.")
        return cleaned

    def clean_reason(self):
        return _validate_meaningful_text(self.cleaned_data.get("reason"), field_label="Причина", required=True)


class EquipmentCheckoutForm(forms.ModelForm):
    class Meta:
        model = EquipmentCheckout
        fields = ["related_request", "equipment", "workplace", "cabinet", "quantity", "taken_at", "due_at", "note"]
        widgets = {
            "taken_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 4, "placeholder": "Необязательные заметки по передаче."}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from operations.models import EquipmentRequest, REQUEST_APPROVED

        queryset = EquipmentRequest.objects.filter(status=REQUEST_APPROVED)
        if user and not user.is_superuser:
            queryset = queryset.filter(requester=user)
        self.fields["related_request"].queryset = queryset
        self.fields["related_request"].label_from_instance = self._format_approved_request_label
        self.fields["related_request"].empty_label = "Выберите одобренную заявку"
        self.fields["equipment"].empty_label = "Выберите оборудование"
        self.fields["workplace"].empty_label = "Выберите рабочее место"
        self.fields["cabinet"].empty_label = "Выберите кабинет"
        self.fields["taken_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["related_request"].help_text = "Здесь доступны только одобренные заявки."
        self.fields["equipment"].help_text = "Должно совпадать с оборудованием в выбранной одобренной заявке."
        self.fields["quantity"].help_text = "Количество задаётся по заявке и правилам выдачи."

    @staticmethod
    def _format_approved_request_label(request_obj: EquipmentRequest) -> str:
        requester = request_obj.requester.get_username() if request_obj.requester_id else "без заявителя"
        equipment = str(request_obj.equipment) if request_obj.equipment_id else "без оборудования"
        requested_dt = timezone.localtime(request_obj.requested_at).strftime("%d.%m.%Y %H:%M") if request_obj.requested_at else "-"
        return (
            f"#{request_obj.pk} | {requester} | {equipment} | "
            f"кол-во: {request_obj.quantity} | {requested_dt}"
        )

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")
        if quantity > MAX_ALLOWED_QUANTITY:
            raise ValidationError(f"Количество не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        related_request = cleaned.get("related_request")
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        if related_request:
            if related_request.status != "approved":
                self.add_error("related_request", "Заявка должна быть одобрена.")
            if equipment and related_request.equipment_id != equipment.id:
                self.add_error("equipment", "Оборудование должно совпадать с одобренной заявкой.")
            if quantity > related_request.quantity:
                self.add_error("quantity", "Количество выдачи превышает количество в заявке.")
        taken_at = cleaned.get("taken_at")
        due_at = cleaned.get("due_at")
        if taken_at and due_at and due_at < taken_at:
            self.add_error("due_at", "Срок возврата не может быть раньше времени выдачи.")
        return cleaned

    def clean_note(self):
        return _validate_meaningful_text(self.cleaned_data.get("note"), field_label="Примечание")
