from django import forms
from django.contrib.auth.models import Group, User

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import Cabinet, EquipmentCategory, Workplace, WorkplaceMember
from operations.models import EquipmentRequest, MaterialUsage
from .authz import ROLE_ALIASES


def _model_fields(model, omit=()):
    blocked = {"deleted_at", *omit}
    return [f.name for f in model._meta.fields if f.editable and f.name not in blocked]


class PortalEquipmentForm(forms.ModelForm):
    VISIBLE_STATUS_CHOICES = (
        ("in_stock", "На складе"),
        ("repair", "В ремонте"),
        ("retired", "Списано"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = self.VISIBLE_STATUS_CHOICES
        self.fields["serial_number"].required = True
        self.fields["purchase_date"].input_formats = ["%Y-%m-%d"]
        self.fields["warranty_end"].input_formats = ["%Y-%m-%d"]
        self.fields["purchase_date"].localize = False
        self.fields["warranty_end"].localize = False
        if self.instance and self.instance.pk:
            if self.instance.purchase_date:
                self.initial["purchase_date"] = self.instance.purchase_date.isoformat()
            if self.instance.warranty_end:
                self.initial["warranty_end"] = self.instance.warranty_end.isoformat()

    class Meta:
        model = Equipment
        fields = _model_fields(Equipment, omit=("inventory_number", "cabinet", "quantity_available"))
        labels = {
            "name": "Название",
            "category": "Категория",
            "serial_number": "Серийный номер",
            "model": "Модель",
            "workplace": "Рабочее место",
            "is_consumable": "Это расходник",
            "status": "Статус",
            "quantity_total": "Количество всего",
            "low_stock_threshold": "Порог остатка",
            "purchase_date": "Дата покупки",
            "warranty_end": "Гарантия до",
            "notes": "Примечание",
            "photo": "Фото",
        }
        widgets = {
            "purchase_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "warranty_end": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 4}),
            # Disable ClearableFileInput "current/clear" checkbox block in portal edit form.
            "photo": forms.FileInput(),
        }

    def clean_serial_number(self):
        value = (self.cleaned_data.get("serial_number") or "").strip()
        if not value:
            raise forms.ValidationError("Укажите серийный номер.")
        qs = Equipment.all_objects.filter(inventory_number=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Такой серийный номер уже существует.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        serial_number = (self.cleaned_data.get("serial_number") or "").strip()
        instance.serial_number = serial_number
        # Keep legacy unique field in sync while UI uses serial number only.
        instance.inventory_number = serial_number
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class PortalEquipmentCategoryForm(forms.ModelForm):
    class Meta:
        model = EquipmentCategory
        fields = _model_fields(EquipmentCategory)
        labels = {"name": "Название", "description": "Описание"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class PortalWorkplaceForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].widget.attrs["readonly"] = "readonly"
        existing_class = self.fields["location"].widget.attrs.get("class", "").strip()
        self.fields["location"].widget.attrs["class"] = f"{existing_class} input-locked".strip()
        self.fields["location"].help_text = "Поле заполняется автоматически через карту."

    class Meta:
        model = Workplace
        fields = _model_fields(Workplace, omit=("deleted_at", "map_address"))
        labels = {
            "name": "Название",
            "location": "Локация",
            "latitude": "Широта",
            "longitude": "Долгота",
            "description": "Описание",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "location": forms.TextInput(attrs={"placeholder": "Адрес будет заполнен с карты"}),
            "latitude": forms.NumberInput(attrs={"step": "0.000001", "placeholder": "55.755826"}),
            "longitude": forms.NumberInput(attrs={"step": "0.000001", "placeholder": "37.617300"}),
        }

    def clean(self):
        cleaned = super().clean()
        location = (cleaned.get("location") or "").strip()
        latitude = cleaned.get("latitude")
        longitude = cleaned.get("longitude")
        if location and (latitude is None or longitude is None):
            self.add_error("location", "Адрес должен быть выбран на карте (с координатами).")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.map_address = (self.cleaned_data.get("location") or "").strip()
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class PortalCabinetForm(forms.ModelForm):
    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            return value
        qs = Cabinet.all_objects.filter(code=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Кабинет с таким названием уже существует.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        normalized_name = (self.cleaned_data.get("name") or "").strip()
        instance.name = normalized_name
        instance.code = normalized_name
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    class Meta:
        model = Cabinet
        fields = _model_fields(Cabinet, omit=("code",))
        labels = {
            "workplace": "Рабочее место",
            "name": "Название",
            "floor": "Этаж",
            "description": "Описание",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class PortalWorkplaceMemberForm(forms.ModelForm):
    class Meta:
        model = WorkplaceMember
        fields = _model_fields(WorkplaceMember)
        labels = {
            "workplace": "Рабочее место",
            "user": "Сотрудник",
            "role": "Роль",
            "assigned_at": "Назначен",
            "note": "Примечание",
        }
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}


class PortalInventoryAdjustmentForm(forms.ModelForm):
    class Meta:
        model = InventoryAdjustment
        fields = _model_fields(InventoryAdjustment)
        labels = {"equipment": "Оборудование", "delta": "Изменение", "reason": "Причина", "created_by": "Кем"}


class PortalEquipmentCheckoutForm(forms.ModelForm):
    class Meta:
        model = EquipmentCheckout
        fields = _model_fields(EquipmentCheckout)
        labels = {
            "equipment": "Оборудование",
            "taken_by": "Кто взял",
            "workplace": "Рабочее место",
            "cabinet": "Кабинет",
            "related_request": "Связанная заявка",
            "quantity": "Количество",
            "taken_at": "Взято",
            "due_at": "Вернуть до",
            "returned_at": "Возвращено",
            "note": "Примечание",
        }
        widgets = {
            "taken_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "returned_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }


class PortalEquipmentRequestForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequest
        fields = _model_fields(EquipmentRequest)
        labels = {
            "requester": "Заявитель",
            "workplace": "Рабочее место",
            "cabinet": "Кабинет",
            "equipment": "Оборудование",
            "quantity": "Количество",
            "request_kind": "Тип заявки",
            "status": "Статус",
            "requested_at": "Создана",
            "needed_by": "Нужно до",
            "comment": "Комментарий",
            "processed_by": "Обработал",
            "processed_at": "Обработано",
        }
        widgets = {
            "needed_by": forms.DateInput(attrs={"type": "date"}),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }


class PortalMaterialUsageForm(forms.ModelForm):
    class Meta:
        model = MaterialUsage
        fields = _model_fields(MaterialUsage)
        labels = {
            "equipment": "Материал",
            "workplace": "Рабочее место",
            "quantity": "Количество",
            "used_by": "Кем списано",
            "used_at": "Дата",
            "related_request": "Связанная заявка",
            "note": "Примечание",
        }
        widgets = {"note": forms.Textarea(attrs={"rows": 3})}


class PortalUserForm(forms.ModelForm):
    password1 = forms.CharField(required=False, strip=False, widget=forms.PasswordInput(), label="Пароль")
    password2 = forms.CharField(required=False, strip=False, widget=forms.PasswordInput(), label="Подтверждение пароля")

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "groups",
        ]
        widgets = {
            "groups": forms.SelectMultiple(attrs={"size": 8}),
        }
        labels = {
            "username": "Имя пользователя",
            "first_name": "Имя",
            "last_name": "Фамилия",
            "email": "Почта",
            "is_active": "Активен",
            "groups": "Группы",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for role_name in ROLE_ALIASES:
            Group.objects.get_or_create(name=role_name)
        role_names = set(ROLE_ALIASES.keys())
        for aliases in ROLE_ALIASES.values():
            role_names.update(aliases)
        self.fields["groups"].queryset = Group.objects.filter(name__in=sorted(role_names)).order_by("name")

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 or p2:
            if not p1:
                self.add_error("password1", "Password is required.")
            if p1 != p2:
                self.add_error("password2", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password1"):
            user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class PortalGroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name", "permissions"]
        widgets = {"permissions": forms.SelectMultiple(attrs={"size": 12})}
        labels = {"name": "Название", "permissions": "Права"}


class RejectStaleRequestsProcedureForm(forms.Form):
    stale_days = forms.IntegerField(label="Дней без обработки", min_value=1, initial=14)


class RestockLowStockConsumablesProcedureForm(forms.Form):
    target_addon = forms.IntegerField(
        label="Запас сверх порога",
        min_value=0,
        initial=0,
        help_text="После пополнения доступный остаток будет доведён до «порог + это число» для каждой позиции ниже порога.",
    )


class CloseStaleIssuedRequestsProcedureForm(forms.Form):
    stale_days = forms.IntegerField(label="Дней в статусе «Выдана»", min_value=1, initial=30)


class FinishAbandonedTimersProcedureForm(forms.Form):
    stale_hours = forms.IntegerField(label="Часов без завершения", min_value=1, initial=12)
