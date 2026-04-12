from django import forms
from django.contrib.auth.models import Group, User

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import Cabinet, EquipmentCategory, Supplier, Workplace, WorkplaceMember
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer


def _model_fields(model, omit=("deleted_at",)):
    return [f.name for f in model._meta.fields if f.editable and f.name not in omit]


class PortalEquipmentForm(forms.ModelForm):
    class Meta:
        model = Equipment
        fields = _model_fields(Equipment, omit=("deleted_at", "cabinet"))
        labels = {
            "name": "Название",
            "inventory_number": "Инвентарный номер",
            "category": "Категория",
            "supplier": "Поставщик",
            "serial_number": "Серийный номер",
            "model": "Модель",
            "workplace": "Рабочее место",
            "is_consumable": "Это расходник",
            "status": "Статус",
            "quantity_total": "Количество всего",
            "quantity_available": "Количество доступно",
            "low_stock_threshold": "Порог остатка",
            "purchase_date": "Дата покупки",
            "warranty_end": "Гарантия до",
            "last_inventory_at": "Последняя инвентаризация",
            "inventory_interval_days": "Интервал инвентаризации (дни)",
            "notes": "Примечание",
            "photo": "Фото",
        }
        widgets = {
            "purchase_date": forms.DateInput(attrs={"type": "date"}),
            "warranty_end": forms.DateInput(attrs={"type": "date"}),
            "last_inventory_at": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_inventory_number(self):
        value = (self.cleaned_data.get("inventory_number") or "").strip()
        if not value:
            return value
        qs = Equipment.all_objects.filter(inventory_number=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Такой инвентарный номер уже существует.")
        return value


class PortalEquipmentCategoryForm(forms.ModelForm):
    class Meta:
        model = EquipmentCategory
        fields = _model_fields(EquipmentCategory)
        labels = {"name": "Название", "description": "Описание"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class PortalSupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = _model_fields(Supplier)
        labels = {
            "name": "Название",
            "contact_name": "Контакт",
            "phone": "Телефон",
            "email": "Почта",
            "address": "Адрес",
            "notes": "Примечание",
        }
        widgets = {"notes": forms.Textarea(attrs={"rows": 4})}


class PortalWorkplaceForm(forms.ModelForm):
    class Meta:
        model = Workplace
        fields = _model_fields(Workplace)
        labels = {"name": "Название", "location": "Локация", "description": "Описание"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class PortalCabinetForm(forms.ModelForm):
    class Meta:
        model = Cabinet
        fields = _model_fields(Cabinet)
        labels = {
            "workplace": "Рабочее место",
            "code": "Код",
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


class PortalWorkTimerForm(forms.ModelForm):
    class Meta:
        model = WorkTimer
        fields = _model_fields(WorkTimer)
        labels = {
            "user": "Пользователь",
            "workplace": "Рабочее место",
            "equipment": "Оборудование",
            "started_at": "Начало",
            "ended_at": "Конец",
            "note": "Примечание",
        }
        widgets = {
            "started_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ended_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }


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
        self.fields["groups"].queryset = Group.objects.filter(
            name__in=["Administrator", "Warehouse", "Sysadmin", "Builder"]
        ).order_by("name")

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


class FinishAbandonedTimersProcedureForm(forms.Form):
    stale_hours = forms.IntegerField(label="Часов без завершения", min_value=1, initial=12)
