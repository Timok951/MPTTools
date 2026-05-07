from django import forms
from django.contrib.auth.models import Group, User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from datetime import timedelta
from django.utils import timezone

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import Cabinet, EmployeeSchedule, EquipmentCategory, RegistrationAllowedEmailDomain, Workplace
from operations.models import REQUEST_PENDING, EquipmentRequest, PeriodicMaterialUsageSchedule
from .authz import ROLE_ALIASES

MAX_ALLOWED_QUANTITY = 1000
MAX_ALLOWED_ADJUSTMENT_DELTA = 1000


def _model_fields(model, omit=()):
    blocked = {"deleted_at", *omit}
    return [f.name for f in model._meta.fields if f.editable and f.name not in blocked]


class PortalEquipmentForm(forms.ModelForm):
    VISIBLE_STATUS_CHOICES = (
        ("in_stock", "На складе"),
        ("repair", "В ремонте"),
        ("retired", "Закончилось"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = self.VISIBLE_STATUS_CHOICES
        self.fields["serial_number"].required = True
        self.fields["quantity_available"].required = False
        self.fields["purchase_date"].input_formats = ["%Y-%m-%d"]
        self.fields["warranty_end"].input_formats = ["%Y-%m-%d"]
        self.fields["purchase_date"].localize = False
        self.fields["warranty_end"].localize = False
        if self.instance and self.instance.pk:
            if self.instance.purchase_date:
                self.initial["purchase_date"] = self.instance.purchase_date.isoformat()
            if self.instance.warranty_end:
                self.initial["warranty_end"] = self.instance.warranty_end.isoformat()
        else:
            today = timezone.localdate()
            self.initial.setdefault("purchase_date", today.isoformat())
            self.initial.setdefault("warranty_end", (today + timedelta(days=365)).isoformat())
            self.initial.setdefault("is_consumable", True)

    class Meta:
        model = Equipment
        fields = _model_fields(Equipment, omit=("inventory_number", "workplace"))
        labels = {
            "name": "Название",
            "category": "Категория",
            "serial_number": "Серийный номер",
            "model": "Модель",
            "is_consumable": "Это расходник",
            "status": "Статус",
            "quantity_total": "Количество всего",
            "quantity_available": "Количество доступно",
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

    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            raise forms.ValidationError("Название не может состоять только из пробелов.")
        return value

    def clean_model(self):
        return (self.cleaned_data.get("model") or "").strip()

    def clean_notes(self):
        return (self.cleaned_data.get("notes") or "").strip()

    def clean_quantity_total(self):
        value = self.cleaned_data.get("quantity_total")
        if value is not None and value > MAX_ALLOWED_QUANTITY:
            raise forms.ValidationError(f"Количество не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return value

    def clean_quantity_available(self):
        value = self.cleaned_data.get("quantity_available")
        if value is not None and value > MAX_ALLOWED_QUANTITY:
            raise forms.ValidationError(f"Количество доступно не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return value

    def clean_low_stock_threshold(self):
        value = self.cleaned_data.get("low_stock_threshold")
        if value is not None and value > MAX_ALLOWED_QUANTITY:
            raise forms.ValidationError(f"Порог не должен превышать {MAX_ALLOWED_QUANTITY}.")
        return value

    def clean_purchase_date(self):
        value = self.cleaned_data.get("purchase_date")
        if value and value > timezone.localdate():
            raise forms.ValidationError("Дата покупки не может быть в будущем.")
        return value

    def clean_warranty_end(self):
        value = self.cleaned_data.get("warranty_end")
        if value and value < timezone.localdate():
            raise forms.ValidationError("Дата окончания гарантии не может быть в прошлом.")
        return value

    def clean(self):
        cleaned = super().clean()
        is_consumable = bool(cleaned.get("is_consumable"))
        qty_total = cleaned.get("quantity_total")
        qty_available = cleaned.get("quantity_available")
        if is_consumable and qty_total is not None:
            cleaned["quantity_available"] = qty_total
        if not is_consumable and qty_available is None and qty_total is not None:
            # При переключении с расходника на нерасходник поле могло быть отключено в браузере.
            # Подставляем разумное значение автоматически вместо тихого отказа сохранения.
            cleaned["quantity_available"] = qty_total
            qty_available = qty_total
        if not is_consumable and qty_available is None:
            self.add_error("quantity_available", "Укажите количество доступно.")
        if qty_total is not None and qty_available is not None and qty_available > qty_total:
            self.add_error("quantity_available", "Количество доступно не может быть больше общего количества.")
        purchase_date = cleaned.get("purchase_date")
        warranty_end = cleaned.get("warranty_end")
        if purchase_date and warranty_end and warranty_end < purchase_date:
            self.add_error("warranty_end", "Гарантия не может заканчиваться раньше даты покупки.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        # Явно фиксируем переключение флага туда/обратно.
        instance.is_consumable = bool(self.cleaned_data.get("is_consumable"))
        serial_number = (self.cleaned_data.get("serial_number") or "").strip()
        instance.serial_number = serial_number
        # Keep legacy unique field in sync while UI uses serial number only.
        instance.inventory_number = serial_number
        # For consumables, "available" is derived from total and not edited manually.
        if instance.is_consumable:
            instance.quantity_available = instance.quantity_total
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class PortalEquipmentCategoryForm(forms.ModelForm):
    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            raise forms.ValidationError("Название не может состоять только из пробелов.")
        return value

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

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

    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            raise forms.ValidationError("Название не может состоять только из пробелов.")
        return value

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

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
            raise forms.ValidationError("Название не может состоять только из пробелов.")
        qs = Cabinet.all_objects.filter(code=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Кабинет с таким названием уже существует.")
        return value

    def clean_floor(self):
        return (self.cleaned_data.get("floor") or "").strip()

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

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


class PortalInventoryAdjustmentForm(forms.ModelForm):
    def clean_delta(self):
        value = self.cleaned_data.get("delta")
        if value is not None and abs(value) > MAX_ALLOWED_ADJUSTMENT_DELTA:
            raise forms.ValidationError(f"Изменение не должно превышать {MAX_ALLOWED_ADJUSTMENT_DELTA} по модулю.")
        return value

    def clean_reason(self):
        value = (self.cleaned_data.get("reason") or "").strip()
        if not value:
            raise forms.ValidationError("Причина не может состоять только из пробелов.")
        return value

    class Meta:
        model = InventoryAdjustment
        fields = _model_fields(InventoryAdjustment)
        labels = {"equipment": "Оборудование", "delta": "Изменение", "reason": "Причина", "created_by": "Кем"}


class PortalEquipmentCheckoutForm(forms.ModelForm):
    def clean_quantity(self):
        value = self.cleaned_data.get("quantity")
        if value is not None and value > MAX_ALLOWED_QUANTITY:
            raise forms.ValidationError(f"Количество не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return value

    def clean_note(self):
        return (self.cleaned_data.get("note") or "").strip()

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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        is_new = not (self.instance and self.instance.pk)
        if is_new:
            self.fields["status"].initial = REQUEST_PENDING
            self.fields["status"].widget = forms.HiddenInput()
            for name in ("requested_at", "processed_by", "processed_at"):
                if name in self.fields:
                    del self.fields[name]
        if "needed_by" in self.fields:
            self.fields["needed_by"].required = True
            if is_new:
                self.fields["needed_by"].initial = timezone.localdate()
        if "non_consumable_target_status" in self.fields:
            self.fields["non_consumable_target_status"].label = "Статус для нерасходуемого"
            self.fields["non_consumable_target_status"].help_text = (
                "Выберите «В ремонте» или «Закончилось». "
                "Для расходников это поле очищается автоматически."
            )
            choices = list(self.fields["non_consumable_target_status"].choices)
            if choices and choices[0][0] != "":
                self.fields["non_consumable_target_status"].choices = [("", "—")] + choices
        if "restock_non_consumable_action" in self.fields:
            self.fields["restock_non_consumable_action"].label = "Действие для пополнения нерасходуемого"
            self.fields["restock_non_consumable_action"].help_text = (
                "По умолчанию увеличивает количество. "
                "Используйте «Перевести на склад», только если нужно сменить статус без увеличения."
            )
            if is_new:
                self.fields["restock_non_consumable_action"].initial = "increase"

    def clean_quantity(self):
        value = self.cleaned_data.get("quantity")
        if value is not None and value > MAX_ALLOWED_QUANTITY:
            raise forms.ValidationError(f"Количество не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return value

    def clean_comment(self):
        return (self.cleaned_data.get("comment") or "").strip()

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        request_kind = (cleaned.get("request_kind") or "").strip()
        if equipment is not None and equipment.is_consumable:
            cleaned["non_consumable_target_status"] = ""
        if request_kind != "restock":
            cleaned["restock_non_consumable_action"] = ""
        elif equipment is not None and not equipment.is_consumable:
            action = (cleaned.get("restock_non_consumable_action") or "").strip()
            if action not in {"increase", "set_in_stock"}:
                cleaned["restock_non_consumable_action"] = "increase"
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.pk:
            instance.status = REQUEST_PENDING
        if commit:
            instance.save()
            self.save_m2m()
        return instance

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
            "non_consumable_target_status": "Статус для нерасходуемого",
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


class PortalPeriodicMaterialUsageScheduleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].queryset = Equipment.objects.filter(is_consumable=True, deleted_at__isnull=True).order_by(
            "name"
        )
        self.fields["workplace"].required = False

    def clean_quantity(self):
        value = self.cleaned_data.get("quantity")
        if value is not None and value > MAX_ALLOWED_QUANTITY:
            raise forms.ValidationError(f"Количество не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return value

    def clean_equipment(self):
        eq = self.cleaned_data.get("equipment")
        if eq is not None and not eq.is_consumable:
            raise forms.ValidationError("Выберите позицию с флагом «расходник».")
        return eq

    class Meta:
        model = PeriodicMaterialUsageSchedule
        fields = ["title", "equipment", "workplace", "quantity", "frequency", "next_run_on"]
        labels = {
            "title": "Название",
            "equipment": "Расходник",
            "workplace": "Рабочее место",
            "quantity": "Количество за раз",
            "frequency": "Периодичность",
            "next_run_on": "Следующее выполнение",
        }
        help_texts = {
            "title": "Например: «10 кабелей в месяц для лаборатории».",
            "next_run_on": "В этот день (и далее каждый месяц) будет автоматически создана заявка на рассмотрение.",
        }
        widgets = {
            "next_run_on": forms.DateInput(attrs={"type": "date"}),
            "title": forms.TextInput(attrs={"placeholder": "Необязательно"}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.is_active = True
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class PortalUserForm(forms.ModelForm):
    password1 = forms.CharField(required=False, strip=False, widget=forms.PasswordInput(), label="Пароль")
    password2 = forms.CharField(required=False, strip=False, widget=forms.PasswordInput(), label="Подтверждение пароля")
    confirm_risky_user_data = forms.BooleanField(
        required=False,
        label="Подтверждаю рискованное создание пользователя (только для отладки)",
        help_text="Используйте только в debug-сценариях: слабый/пустой пароль, неполные ФИО или проблемная почта.",
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "groups",
            "confirm_risky_user_data",
        ]
        widgets = {
            "groups": forms.SelectMultiple(attrs={"size": 6}),
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
        is_create = not (self.instance and self.instance.pk)
        if p1 or p2:
            if not p1:
                self.add_error("password1", "Укажите пароль.")
            if p1 != p2:
                self.add_error("password2", "Пароли не совпадают.")

        risk_reasons = []
        if is_create and not p1:
            risk_reasons.append("Пользователь создаётся без пароля.")
        if p1:
            probe_user = self.instance if (self.instance and self.instance.pk) else User(username=cleaned.get("username") or "")
            try:
                validate_password(p1, user=probe_user)
            except DjangoValidationError as exc:
                first_msg = (exc.messages or ["Слишком слабый пароль."])[0]
                risk_reasons.append(f"Пароль слабый: {first_msg}")

        first_name = (cleaned.get("first_name") or "").strip()
        last_name = (cleaned.get("last_name") or "").strip()
        if not first_name or not last_name:
            risk_reasons.append("Не заполнены имя и/или фамилия.")

        email = (cleaned.get("email") or "").strip()
        if not email:
            risk_reasons.append("Не указан email для связи и восстановления доступа.")
        else:
            duplicate_qs = User.objects.filter(email__iexact=email)
            if self.instance and self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                risk_reasons.append("Такой email уже используется другим пользователем.")

        if risk_reasons and not cleaned.get("confirm_risky_user_data"):
            self.add_error(
                "confirm_risky_user_data",
                "Подтвердите рискованное создание или исправьте данные.",
            )
            self.add_error(
                None,
                "Обнаружены риски: " + "; ".join(risk_reasons),
            )
        return cleaned

    def clean_username(self):
        value = (self.cleaned_data.get("username") or "").strip()
        if not value:
            raise forms.ValidationError("Имя пользователя не может состоять только из пробелов.")
        return value

    def clean_first_name(self):
        return (self.cleaned_data.get("first_name") or "").strip()

    def clean_last_name(self):
        return (self.cleaned_data.get("last_name") or "").strip()

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip()

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password1"):
            user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class PortalGroupForm(forms.ModelForm):
    def clean_name(self):
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            raise forms.ValidationError("Название группы не может состоять только из пробелов.")
        return value

    class Meta:
        model = Group
        fields = ["name", "permissions"]
        widgets = {"permissions": forms.SelectMultiple(attrs={"size": 12})}
        labels = {"name": "Название", "permissions": "Права"}


class PortalRegistrationAllowedEmailDomainForm(forms.ModelForm):
    """Домены почты для регистрации и восстановления пароля по коду."""

    class Meta:
        model = RegistrationAllowedEmailDomain
        fields = ["domain", "is_active", "notes"]
        labels = {
            "domain": "Домен",
            "is_active": "Разрешён",
            "notes": "Заметка",
        }
        help_texts = {
            "domain": "Без символа @, например mpt.ru или partner.company.ru.",
            "is_active": "Если снять флажок, домен не будет приниматься при регистрации и сбросе пароля.",
            "notes": "Необязательно: для кого домен или комментарий для коллег.",
        }

    def clean_domain(self):
        value = self.cleaned_data.get("domain") or ""
        d = value.strip().lower().lstrip("@")
        if not d:
            raise forms.ValidationError("Укажите домен.")
        if "@" in d or "/" in d or " " in d:
            raise forms.ValidationError("Укажите только имя домена, без @ и пути.")
        return d

    def clean_notes(self):
        return (self.cleaned_data.get("notes") or "").strip()


class PortalEmployeeScheduleForm(forms.ModelForm):
    custom_weekdays = forms.MultipleChoiceField(
        choices=EmployeeSchedule.WEEKDAY_CHOICES,
        required=False,
        label="Кастомные рабочие дни",
        help_text="Используется только для режима «Кастомный».",
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = EmployeeSchedule
        fields = ["user", "schedule_type", "cycle_start_date", "custom_weekdays", "is_active"]
        labels = {
            "user": "Сотрудник",
            "schedule_type": "Режим графика",
            "cycle_start_date": "Дата начала цикла 2/2",
            "is_active": "Включить контроль по графику",
        }
        help_texts = {
            "is_active": "Если снять флажок — расписание отключается, сотрудник считается всегда рабочим.",
        }
        widgets = {
            "cycle_start_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(is_active=True).order_by("username")
        self.fields["cycle_start_date"].input_formats = ["%Y-%m-%d"]
        self.fields["cycle_start_date"].localize = False
        if not (self.instance and self.instance.pk):
            self.initial.setdefault("cycle_start_date", timezone.localdate().isoformat())
        if self.instance and self.instance.pk:
            self.initial["custom_weekdays"] = [part for part in (self.instance.custom_workdays or "").split(",") if part]

    def clean(self):
        cleaned = super().clean()
        schedule_type = cleaned.get("schedule_type")
        weekdays = cleaned.get("custom_weekdays") or []
        cycle_start = cleaned.get("cycle_start_date")
        if cycle_start and cycle_start > timezone.localdate():
            self.add_error("cycle_start_date", "Дата начала цикла не может быть в будущем.")
        if schedule_type != EmployeeSchedule.SCHEDULE_CUSTOM and not weekdays:
            if self.instance and self.instance.pk and self.instance.custom_workdays:
                weekdays = [part for part in self.instance.custom_workdays.split(",") if part]
            else:
                weekdays = ["0", "1", "2", "3", "4"]
        if schedule_type == EmployeeSchedule.SCHEDULE_CUSTOM and not weekdays:
            self.add_error("custom_weekdays", "Для кастомного графика выберите хотя бы один рабочий день.")
        cleaned["custom_weekdays"] = weekdays
        cleaned["custom_workdays"] = ",".join(sorted(set(weekdays)))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.custom_workdays = self.cleaned_data.get("custom_workdays", "")
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class RejectStaleRequestsProcedureForm(forms.Form):
    stale_days = forms.IntegerField(label="Дней без обработки", min_value=1, initial=14)


class RestockLowStockConsumablesProcedureForm(forms.Form):
    fixed_increase = forms.IntegerField(
        label="Увеличить на (шт.)",
        min_value=1,
        initial=5,
        help_text="Для каждой позиции с низким остатком будет создано пополнение на фиксированное количество единиц.",
    )


class SimpleRestockAndRecoverProcedureForm(forms.Form):
    NON_CONSUMABLE_ACTION_CHOICES = (
        ("set_in_stock", "Перевести в «На складе»"),
        ("increase", "Пополнить количество"),
    )

    equipment = forms.ModelChoiceField(
        queryset=Equipment.objects.none(),
        label="Оборудование",
    )
    quantity = forms.IntegerField(
        label="Количество пополнения",
        min_value=1,
        initial=1,
        help_text="Для расходника всегда пополняется остаток. Для нерасходника используется режим ниже.",
    )
    non_consumable_action = forms.ChoiceField(
        label="Для нерасходника",
        choices=NON_CONSUMABLE_ACTION_CHOICES,
        initial="set_in_stock",
        help_text="Если выбран расходник, это поле игнорируется.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Equipment.objects.select_related("workplace").filter(deleted_at__isnull=True).order_by("name", "inventory_number")
        self.fields["equipment"].queryset = qs
        self.fields["equipment"].label_from_instance = (
            lambda item: f"{item.name} | доступно: {item.quantity_available} из {item.quantity_total}"
        )

    def clean_quantity(self):
        value = self.cleaned_data.get("quantity") or 0
        if value > MAX_ALLOWED_QUANTITY:
            raise forms.ValidationError(f"Количество не должно превышать {MAX_ALLOWED_QUANTITY}.")
        return value


class FinishAbandonedTimersProcedureForm(forms.Form):
    stale_hours = forms.IntegerField(label="Часов без завершения", min_value=1, initial=12)
