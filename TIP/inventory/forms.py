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
    MaterialUsage,
    REQUEST_APPROVED,
    REQUEST_CLOSED,
    REQUEST_ISSUED,
)


def _lang_label(ru_text: str, en_text: str, language_code: str) -> str:
    return en_text if str(language_code).lower().startswith("en") else ru_text


class RussianAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label="Имя пользователя", widget=forms.TextInput(attrs={"autofocus": True}))
    password = forms.CharField(
        label="Пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )


class RussianUserCreationForm(UserCreationForm):
    username = forms.CharField(label="Имя пользователя", help_text="Обязательно. Не более 150 символов.")
    password1 = forms.CharField(
        label="Пароль", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    password2 = forms.CharField(
        label="Подтверждение пароля",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Введите пароль ещё раз для проверки.",
    )


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
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "user@example.com"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_user = user

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        user = self._request_user
        if user and getattr(user, "is_authenticated", False):
            profile_email = (getattr(user, "email", None) or "").strip().lower()
            if profile_email:
                if email != profile_email:
                    raise ValidationError("Введите email, указанный в вашем профиле.")
            elif User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                raise ValidationError("Этот email уже используется другой учётной записью.")
        return email


class PasswordResetConfirmForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "user@example.com"}),
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
        self.fields["default_checkout_status"].label = t("Фильтр выдач по умолчанию", "Default checkout filter")
        self.fields["default_checkout_status"].choices = [
            ("", t("Все выдачи", "All checkouts")),
            ("active", t("Активные выдачи", "Active checkouts")),
            ("returned", t("Возвращённые выдачи", "Returned checkouts")),
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
            initial=self.instance.default_request_status if self.instance and self.instance.pk else "",
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
            "default_checkout_status",
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
        self.fields["quantity"].help_text = "Запрошенное количество не может превышать доступный остаток."
        self.fields["needed_by"].label = "Нужно до"
        self.fields["needed_by"].help_text = "Необязательная плановая дата, когда оборудование должно понадобиться."
        self.fields["comment"].label = "Комментарий"
        self.fields["comment"].help_text = "Добавьте детали, которые помогут быстрее согласовать заявку."

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        if equipment and quantity > equipment.quantity_available:
            self.add_error("quantity", "Запрошенное количество превышает доступный остаток.")
        return cleaned


class EquipmentRequestMessageForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequestMessage
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 3, "placeholder": "Добавьте сообщение по заявке."}),
        }


class EquipmentRequestPhotoForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequestPhoto
        fields = ["image", "caption"]
        widgets = {
            "caption": forms.TextInput(attrs={"placeholder": "Подпись к фото (необязательно)."}),
        }


class MaterialUsageForm(forms.ModelForm):
    class Meta:
        model = MaterialUsage
        fields = ["equipment", "workplace", "quantity", "related_request", "note"]
        labels = {
            "equipment": "Оборудование",
            "workplace": "Рабочее место",
            "quantity": "Количество",
            "related_request": "Связанная заявка",
            "note": "Комментарий",
        }
        widgets = {
            "note": forms.Textarea(attrs={"rows": 4, "placeholder": "Необязательная причина или детали списания."}),
        }

    def __init__(self, *args, **kwargs):
        initial_request_id = kwargs.pop("initial_request_id", None)
        super().__init__(*args, **kwargs)
        self.fields["equipment"].empty_label = "Выберите оборудование"
        self.fields["workplace"].empty_label = "Выберите рабочее место"
        request_base_qs = (
            EquipmentRequest.objects.filter(status__in=[REQUEST_APPROVED, REQUEST_ISSUED, REQUEST_CLOSED])
            .order_by("-requested_at")
        )
        self.fields["related_request"].queryset = request_base_qs.select_related("equipment", "requester")
        self.fields["related_request"].empty_label = "Без связанной заявки"
        request_rows = request_base_qs.values("id", "quantity", "equipment_id", "workplace_id")
        self.request_quantity_map = {str(item["id"]): item["quantity"] for item in request_rows}
        request_rows = request_base_qs.values("id", "quantity", "equipment_id", "workplace_id")
        self.request_equipment_map = {str(item["id"]): (item["equipment_id"] or "") for item in request_rows}
        request_rows = request_base_qs.values("id", "quantity", "equipment_id", "workplace_id")
        self.request_workplace_map = {str(item["id"]): (item["workplace_id"] or "") for item in request_rows}
        if initial_request_id:
            self.initial["related_request"] = initial_request_id
        self.fields["quantity"].help_text = (
            "Для расходуемого оборудования укажите объём выдачи. "
            "Если выбрана заявка, количество автоматически берётся из неё."
        )
        self.fields["related_request"].help_text = (
            "Необязательно. Если выбрана заявка, оборудование, рабочее место и количество подставятся автоматически."
        )

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        related_request = cleaned.get("related_request")
        quantity = cleaned.get("quantity") or 0
        if related_request:
            quantity = related_request.quantity
            cleaned["quantity"] = quantity
        if equipment and quantity > equipment.quantity_available:
            self.add_error("quantity", "Недостаточно доступного остатка.")
        if equipment and not equipment.is_consumable:
            note = (cleaned.get("note") or "").strip()
            if not note:
                self.add_error("note", "Для нерасходуемого оборудования укажите причину списания (например, сломано).")
            if quantity != 1:
                self.add_error("quantity", "Нерасходуемое оборудование списывается поштучно (количество = 1).")
        if related_request and equipment and related_request.equipment_id != equipment.id:
            self.add_error("equipment", "Оборудование должно совпадать с выбранной заявкой.")
        if related_request and related_request.status in {"pending", "rejected"}:
            self.add_error("related_request", "Операция доступна только для обработанных заявок.")
        return cleaned


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
        if equipment and delta is not None:
            new_total = equipment.quantity_total + delta
            new_available = equipment.quantity_available + delta
            if new_total < 0 or new_available < 0:
                raise ValidationError("Корректировка приведёт к отрицательному остатку.")
        return cleaned


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
        self.fields["related_request"].empty_label = "Выберите одобренную заявку"
        self.fields["equipment"].empty_label = "Выберите оборудование"
        self.fields["workplace"].empty_label = "Выберите рабочее место"
        self.fields["cabinet"].empty_label = "Выберите кабинет"
        self.fields["taken_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["related_request"].help_text = "Здесь доступны только одобренные заявки."
        self.fields["equipment"].help_text = "Должно совпадать с оборудованием в выбранной одобренной заявке."
        self.fields["quantity"].help_text = "Не может превышать ни количество в заявке, ни доступный остаток."

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        related_request = cleaned.get("related_request")
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        if equipment and quantity > equipment.quantity_available:
            self.add_error("quantity", "Недостаточно доступного остатка для выдачи.")
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
