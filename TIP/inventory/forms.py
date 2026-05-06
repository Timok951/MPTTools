from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from django.contrib.auth.models import User

from core.models import DirectMessage, UserPreference, Workplace
from operations.models import (
    EquipmentRequest,
    EquipmentRequestMessage,
    EquipmentRequestPhoto,
    REQUEST_APPROVED,
)

MAX_ALLOWED_QUANTITY = 1000
MAX_ALLOWED_ADJUSTMENT_DELTA = 1000

# Корпоративная почта: регистрация и восстановление пароля (гостевой сценарий).
REGISTRATION_EMAIL_DOMAIN = "mpt.ru"


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _email_has_allowed_domain(email: str) -> bool:
    return email.endswith(f"@{REGISTRATION_EMAIL_DOMAIN}")


def _validate_corporate_email(email: str) -> str:
    if not _email_has_allowed_domain(email):
        raise ValidationError(f"Разрешены адреса только на домене @{REGISTRATION_EMAIL_DOMAIN}.")
    return email


def _lang_label(ru_text: str, en_text: str, language_code: str) -> str:
    return en_text if str(language_code).lower().startswith("en") else ru_text


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
        help_text=f"Обязательно. Только корпоративный адрес @{REGISTRATION_EMAIL_DOMAIN}.",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": f"user@{REGISTRATION_EMAIL_DOMAIN}"}),
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

    def clean_email(self):
        email = _normalize_email(self.cleaned_data.get("email"))
        _validate_corporate_email(email)
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("Пользователь с таким адресом почты уже зарегистрирован.")
        return email


class BackupImportForm(forms.Form):
    backup_file = forms.FileField(label=_("JSON backup"))

    def clean_backup_file(self):
        backup_file = self.cleaned_data["backup_file"]
        if not backup_file.name.lower().endswith(".json"):
            raise ValidationError(_("Upload a JSON backup file."))
        return backup_file


class PostgresqlDumpImportForm(forms.Form):
    dump_file = forms.FileField(label=_("PostgreSQL dump (.dump, custom format)"))

    def clean_dump_file(self):
        f = self.cleaned_data["dump_file"]
        name = (f.name or "").lower()
        if not name.endswith(".dump"):
            raise ValidationError(_("Upload a .dump file (pg_dump -Fc)."))
        return f


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={"autocomplete": "email", "placeholder": f"user@{REGISTRATION_EMAIL_DOMAIN}"},
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
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
            attrs={"autocomplete": "email", "placeholder": f"user@{REGISTRATION_EMAIL_DOMAIN}"},
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
        body = (self.cleaned_data.get("body") or "").strip()
        if not body:
            raise ValidationError("Сообщение не может состоять только из пробелов.")
        return body


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
                ("issued", t("Выдана", "Issued")),
                ("closed", t("Закрыта", "Closed")),
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
            "Период истории списаний по умолчанию",
            "Default usage history period",
        )
        self.fields["default_usage_period_days"].help_text = t(
            "Автоматически подставлять период истории списаний от текущей даты.",
            "Automatically prefill the usage history period from the current date.",
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
    class Meta:
        model = EquipmentRequest
        fields = ["workplace", "cabinet", "equipment", "quantity", "request_kind", "needed_by", "comment"]
        widgets = {
            "needed_by": forms.DateInput(attrs={"type": "date"}),
            "comment": forms.Textarea(attrs={"rows": 4, "placeholder": "Опишите, что требуется и почему."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["workplace"].label = "Рабочее место"
        self.fields["workplace"].empty_label = "Выберите рабочее место"
        self.fields["cabinet"].label = "Кабинет"
        self.fields["cabinet"].empty_label = "Выберите кабинет (необязательно)"
        self.fields["request_kind"].label = "Тип заявки"
        self.fields["equipment"].label = "Оборудование"
        self.fields["equipment"].empty_label = "Выберите оборудование"
        self.fields["quantity"].label = "Количество"
        self.fields["quantity"].help_text = "Укажите нужное количество для склада."
        self.fields["needed_by"].label = "Нужно до"
        self.fields["needed_by"].required = True
        self.fields["needed_by"].help_text = "Обязательная дата, к которой желательно получить материал (по умолчанию — сегодня)."
        if not self.is_bound and not self.initial.get("needed_by") and not (self.instance and self.instance.pk):
            self.initial["needed_by"] = timezone.localdate()
        self.fields["comment"].label = "Комментарий"
        self.fields["comment"].help_text = "Добавьте детали, которые помогут быстрее согласовать заявку."

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")
        if quantity > MAX_ALLOWED_QUANTITY:
            raise ValidationError(f"Количество не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return quantity

    def clean_comment(self):
        comment = (self.cleaned_data.get("comment") or "").strip()
        return comment

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        return cleaned


class EquipmentRequestMessageForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequestMessage
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 3, "placeholder": "Добавьте сообщение по заявке."}),
        }

    def clean_body(self):
        body = (self.cleaned_data.get("body") or "").strip()
        if not body:
            raise ValidationError("Сообщение не может состоять только из пробелов.")
        return body


class EquipmentRequestPhotoForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequestPhoto
        fields = ["image", "caption"]
        widgets = {
            "caption": forms.TextInput(attrs={"placeholder": "Подпись к фото (необязательно)."}),
        }

    def clean_caption(self):
        caption = (self.cleaned_data.get("caption") or "").strip()
        return caption


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
        self.fields["delta"].help_text = "Используйте положительные числа для пополнения и отрицательные для списания."
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
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise ValidationError("Укажите причину корректировки.")
        return reason


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
        note = (self.cleaned_data.get("note") or "").strip()
        return note
