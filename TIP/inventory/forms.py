from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from django.contrib.auth.models import User

from core.models import DirectMessage, UserPreference, Workplace
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer


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


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "user@example.com"}),
    )


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


class QuickTimerStartForm(forms.Form):
    workplace = forms.ModelChoiceField(
        label="Рабочее место",
        queryset=Workplace.objects.order_by("name"),
        required=False,
        empty_label="Выберите рабочее место",
    )
    equipment = forms.ModelChoiceField(
        label="Оборудование",
        queryset=Equipment.objects.order_by("name"),
        required=False,
        empty_label="Выберите оборудование",
    )
    note = forms.CharField(
        label="Примечание",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Над чем вы сейчас работаете?"}),
    )


class UserPreferenceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        language_code = kwargs.pop("language_code", "ru")
        super().__init__(*args, **kwargs)
        t = lambda ru_text, en_text: _lang_label(ru_text, en_text, language_code)

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
        self.fields["default_timer_status"].label = t("Фильтр таймеров по умолчанию", "Default timer filter")
        self.fields["default_timer_status"].choices = [
            ("", t("Все таймеры", "All timers")),
            ("active", t("Активные таймеры", "Active timers")),
            ("finished", t("Завершённые таймеры", "Finished timers")),
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

    class Meta:
        model = UserPreference
        fields = [
            "theme_variant",
            "preferred_language",
            "page_size",
            "date_display_format",
            "default_timer_status",
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
        fields = ["workplace", "equipment", "quantity", "request_kind", "needed_by", "comment"]
        widgets = {
            "needed_by": forms.DateInput(attrs={"type": "date"}),
            "comment": forms.Textarea(attrs={"rows": 4, "placeholder": "Опишите, что требуется и почему."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["workplace"].empty_label = "Выберите рабочее место"
        self.fields["equipment"].empty_label = "Выберите оборудование"
        self.fields["quantity"].help_text = "Запрошенное количество не может превышать доступный остаток."
        self.fields["needed_by"].help_text = "Необязательная плановая дата, когда оборудование должно понадобиться."
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


class MaterialUsageForm(forms.ModelForm):
    class Meta:
        model = MaterialUsage
        fields = ["equipment", "workplace", "quantity", "related_request", "note"]
        widgets = {
            "note": forms.Textarea(attrs={"rows": 4, "placeholder": "Необязательная причина или детали списания."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].queryset = self.fields["equipment"].queryset.filter(is_consumable=True)
        self.fields["equipment"].empty_label = "Выберите расходник"
        self.fields["workplace"].empty_label = "Выберите рабочее место"
        self.fields["related_request"].empty_label = "Без связанной заявки"
        self.fields["quantity"].help_text = "Здесь можно списывать только расходные материалы."
        self.fields["related_request"].help_text = "При наличии привяжите списание к одобренной заявке."

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Количество должно быть положительным.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        if equipment and not equipment.is_consumable:
            self.add_error("equipment", "Списывать можно только расходники. Для многоразового инструмента используйте выдачу/возврат.")
        if equipment and quantity > equipment.quantity_available:
            self.add_error("quantity", "Недостаточно доступного остатка.")
        return cleaned


class InventoryAdjustmentForm(forms.ModelForm):
    class Meta:
        model = InventoryAdjustment
        fields = ["equipment", "delta", "reason"]
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


class WorkTimerForm(forms.ModelForm):
    class Meta:
        model = WorkTimer
        fields = ["workplace", "equipment", "started_at", "ended_at", "note"]
        widgets = {
            "started_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "ended_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 4, "placeholder": "Необязательные заметки о рабочей сессии."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["workplace"].empty_label = "Выберите рабочее место"
        self.fields["equipment"].empty_label = "Выберите оборудование"
        self.fields["started_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["ended_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["started_at"].initial = self.fields["started_at"].initial or timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        self.fields["ended_at"].help_text = "Оставьте пустым, если таймер ещё работает."
        self.fields["note"].help_text = "Для большинства живых задач используйте быстрый старт. Эта форма удобнее для ручного ввода."

    def clean(self):
        cleaned = super().clean()
        started_at = cleaned.get("started_at")
        ended_at = cleaned.get("ended_at")
        if started_at and ended_at and ended_at < started_at:
            raise ValidationError("Время завершения не может быть раньше времени начала.")
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
