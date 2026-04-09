from django import forms
from django.core.exceptions import ValidationError
from assets.models import EquipmentCheckout, InventoryAdjustment
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer


class EquipmentRequestForm(forms.ModelForm):
    class Meta:
        model = EquipmentRequest
        fields = ["workplace", "equipment", "quantity", "request_kind", "needed_by", "comment"]
        widgets = {
            "needed_by": forms.DateInput(attrs={"type": "date"}),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }

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
            "note": forms.Textarea(attrs={"rows": 3}),
        }

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
            self.add_error("quantity", "Not enough available stock.")
        return cleaned


class InventoryAdjustmentForm(forms.ModelForm):
    class Meta:
        model = InventoryAdjustment
        fields = ["equipment", "delta", "reason"]

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
            "started_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ended_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

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
            "taken_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "note": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from operations.models import EquipmentRequest, REQUEST_APPROVED

        queryset = EquipmentRequest.objects.filter(status=REQUEST_APPROVED)
        if user and not user.is_superuser:
            queryset = queryset.filter(requester=user)
        self.fields["related_request"].queryset = queryset

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
