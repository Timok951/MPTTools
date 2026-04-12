from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import UserPreference, Workplace
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer


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


class QuickTimerStartForm(forms.Form):
    workplace = forms.ModelChoiceField(
        label=_("Workplace"),
        queryset=Workplace.objects.order_by("name"),
        required=False,
        empty_label=_("Select workplace"),
    )
    equipment = forms.ModelChoiceField(
        label=_("Equipment"),
        queryset=Equipment.objects.order_by("name"),
        required=False,
        empty_label=_("Select equipment"),
    )
    note = forms.CharField(
        label=_("Note"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": _("What are you working on?")}),
    )


class UserPreferenceForm(forms.ModelForm):
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["theme_variant"].label = "Тема"
        self.fields["theme_variant"].help_text = "Светлая, контрастная или тёмная тема интерфейса."
        self.fields["preferred_language"] = forms.ChoiceField(
            label="Язык интерфейса",
            choices=[("ru", "Русский"), ("en", "English")],
            initial=self.instance.preferred_language if self.instance and self.instance.pk else "ru",
        )
        self.fields["page_size"].label = "Размер страницы"
        self.fields["date_display_format"].label = "Формат даты"
        self.fields["default_timer_status"].label = "Фильтр таймеров по умолчанию"
        self.fields["default_checkout_status"].label = "Фильтр выдач по умолчанию"
        self.fields["hotkeys_enabled"].label = "Включить горячие клавиши"
        self.fields["show_hotkey_legend"].label = "Показывать подсказку по горячим клавишам"
        self.fields["default_request_status"] = forms.ChoiceField(
            label="Статус заявок по умолчанию",
            required=False,
            choices=[("", "Все заявки")] + list(EquipmentRequest._meta.get_field("status").choices),
            initial=self.instance.default_request_status if self.instance and self.instance.pk else "",
        )
        self.fields["default_request_kind"] = forms.ChoiceField(
            label="Тип заявок по умолчанию",
            required=False,
            choices=[("", "Все типы заявок")] + list(EquipmentRequest._meta.get_field("request_kind").choices),
            initial=self.instance.default_request_kind if self.instance and self.instance.pk else "",
        )
        self.fields["default_usage_period_days"].label = "Период истории списаний по умолчанию"
        self.fields["default_usage_period_days"].help_text = "Автоматически подставлять период истории списаний от текущей даты."
        self.fields["hotkeys_enabled"].help_text = "Включить глобальные горячие клавиши вне полей формы."
        self.fields["show_hotkey_legend"].help_text = "Показывать в интерфейсе подсказку по горячим клавишам."


class EquipmentRequestForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequest
        fields = ["workplace", "equipment", "quantity", "request_kind", "needed_by", "comment"]
        widgets = {
            "needed_by": forms.DateInput(attrs={"type": "date"}),
            "comment": forms.Textarea(attrs={"rows": 4, "placeholder": "Describe what you need and why."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["workplace"].empty_label = "Select workplace"
        self.fields["equipment"].empty_label = "Select equipment"
        self.fields["quantity"].help_text = "Requested quantity cannot exceed available stock."
        self.fields["needed_by"].help_text = "Optional planned date for when the item is needed."
        self.fields["comment"].help_text = "Add details that help approve the request faster."

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Quantity must be positive.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        if equipment and quantity > equipment.quantity_available:
            self.add_error("quantity", "Requested quantity exceeds available stock.")
        return cleaned


class MaterialUsageForm(forms.ModelForm):
    class Meta:
        model = MaterialUsage
        fields = ["equipment", "workplace", "quantity", "related_request", "note"]
        widgets = {
            "note": forms.Textarea(attrs={"rows": 4, "placeholder": "Optional reason or usage details."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].queryset = self.fields["equipment"].queryset.filter(is_consumable=True)
        self.fields["equipment"].empty_label = "Select consumable"
        self.fields["workplace"].empty_label = "Select workplace"
        self.fields["related_request"].empty_label = "No linked request"
        self.fields["quantity"].help_text = "Only consumables can be written off here."
        self.fields["related_request"].help_text = "Link the usage to an approved request when available."

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Quantity must be positive.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        if equipment and not equipment.is_consumable:
            self.add_error("equipment", "Списывать можно только расходники. Для многоразового инструмента используйте выдачу/возврат.")
        if equipment and quantity > equipment.quantity_available:
            self.add_error("quantity", "Not enough available stock.")
        return cleaned


class InventoryAdjustmentForm(forms.ModelForm):
    class Meta:
        model = InventoryAdjustment
        fields = ["equipment", "delta", "reason"]
        widgets = {
            "reason": forms.TextInput(attrs={"placeholder": "Why is the stock being corrected?"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].empty_label = "Select equipment"
        self.fields["delta"].help_text = "Use positive numbers to add stock and negative to subtract."
        self.fields["reason"].help_text = "This text is shown in inventory history and audit logs."

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        delta = cleaned.get("delta")
        if equipment and delta is not None:
            new_total = equipment.quantity_total + delta
            new_available = equipment.quantity_available + delta
            if new_total < 0 or new_available < 0:
                raise ValidationError("Adjustment would make stock negative.")
        return cleaned


class WorkTimerForm(forms.ModelForm):
    class Meta:
        model = WorkTimer
        fields = ["workplace", "equipment", "started_at", "ended_at", "note"]
        widgets = {
            "started_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "ended_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 4, "placeholder": "Optional notes about the work session."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["workplace"].empty_label = "Select workplace"
        self.fields["equipment"].empty_label = "Select equipment"
        self.fields["started_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["ended_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["started_at"].initial = self.fields["started_at"].initial or timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        self.fields["ended_at"].help_text = "Leave empty if the timer is still running."
        self.fields["note"].help_text = "Use the quick-start form for most live work. This screen is best for manual entries."

    def clean(self):
        cleaned = super().clean()
        started_at = cleaned.get("started_at")
        ended_at = cleaned.get("ended_at")
        if started_at and ended_at and ended_at < started_at:
            raise ValidationError("End time cannot be before start time.")
        return cleaned


class EquipmentCheckoutForm(forms.ModelForm):
    class Meta:
        model = EquipmentCheckout
        fields = ["related_request", "equipment", "workplace", "cabinet", "quantity", "taken_at", "due_at", "note"]
        widgets = {
            "taken_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
            "note": forms.Textarea(attrs={"rows": 4, "placeholder": "Optional handover notes."}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from operations.models import EquipmentRequest, REQUEST_APPROVED

        queryset = EquipmentRequest.objects.filter(status=REQUEST_APPROVED)
        if user and not user.is_superuser:
            queryset = queryset.filter(requester=user)
        self.fields["related_request"].queryset = queryset
        self.fields["related_request"].empty_label = "Select approved request"
        self.fields["equipment"].empty_label = "Select equipment"
        self.fields["workplace"].empty_label = "Select workplace"
        self.fields["cabinet"].empty_label = "Select cabinet"
        self.fields["taken_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["due_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["related_request"].help_text = "Only approved requests are available here."
        self.fields["equipment"].help_text = "Must match the selected approved request."
        self.fields["quantity"].help_text = "Cannot exceed either the request quantity or available stock."

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity") or 0
        if quantity <= 0:
            raise ValidationError("Quantity must be positive.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        related_request = cleaned.get("related_request")
        equipment = cleaned.get("equipment")
        quantity = cleaned.get("quantity") or 0
        if equipment and quantity > equipment.quantity_available:
            self.add_error("quantity", "Not enough available stock for checkout.")
        if related_request:
            if related_request.status != "approved":
                self.add_error("related_request", "Request must be approved.")
            if equipment and related_request.equipment_id != equipment.id:
                self.add_error("equipment", "Equipment must match the approved request.")
            if quantity > related_request.quantity:
                self.add_error("quantity", "Checkout quantity exceeds request.")
        taken_at = cleaned.get("taken_at")
        due_at = cleaned.get("due_at")
        if taken_at and due_at and due_at < taken_at:
            self.add_error("due_at", "Due time cannot be before taken time.")
        return cleaned
