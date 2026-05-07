from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import (
    Cabinet,
    DirectMessage,
    EmployeeSchedule,
    EquipmentCategory,
    RegistrationAllowedEmailDomain,
    UserPreference,
    Workplace,
)
from operations.models import (
    REQUEST_PENDING,
    EquipmentRequest,
    EquipmentRequestMessage,
    EquipmentRequestPhoto,
    MaterialUsage,
    PeriodicMaterialUsageSchedule,
)


class AuditActorModelSerializer(serializers.ModelSerializer):
    def _full_clean_instance(self, instance, *, partial: bool = False):
        # Для partial update не валидируем поля, которые не передавали в payload.
        exclude = None
        if partial and hasattr(self, "initial_data"):
            model_fields = {f.name for f in instance._meta.fields}
            provided = {k for k in self.initial_data.keys() if k in model_fields}
            exclude = list(model_fields - provided)
        try:
            instance.full_clean(exclude=exclude)
        except DjangoValidationError as exc:
            payload = getattr(exc, "message_dict", None) or {"non_field_errors": list(exc.messages)}
            raise ValidationError(payload)

    def create(self, validated_data):
        actor = validated_data.pop("_actor", None)
        instance = self.Meta.model(**validated_data)
        if actor is not None:
            instance._actor = actor
        self._full_clean_instance(instance, partial=False)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        actor = validated_data.pop("_actor", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if actor is not None:
            instance._actor = actor
        self._full_clean_instance(instance, partial=getattr(self, "partial", False))
        instance.save()
        return instance


class WorkplaceSerializer(AuditActorModelSerializer):
    class Meta:
        model = Workplace
        fields = ["id", "name", "location", "description", "deleted_at"]
        read_only_fields = ["deleted_at"]


class CabinetSerializer(AuditActorModelSerializer):
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)

    class Meta:
        model = Cabinet
        fields = ["id", "code", "name", "floor", "description", "workplace", "workplace_name", "deleted_at"]
        read_only_fields = ["deleted_at"]


class EquipmentCategorySerializer(AuditActorModelSerializer):
    class Meta:
        model = EquipmentCategory
        fields = ["id", "name", "description", "deleted_at"]
        read_only_fields = ["deleted_at"]


class EquipmentSerializer(AuditActorModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)

    class Meta:
        model = Equipment
        fields = [
            "id",
            "name",
            "inventory_number",
            "category",
            "category_name",
            "serial_number",
            "model",
            "workplace",
            "workplace_name",
            "is_consumable",
            "status",
            "quantity_total",
            "quantity_available",
            "low_stock_threshold",
            "purchase_date",
            "warranty_end",
            "notes",
            "created_at",
            "updated_at",
            "deleted_at",
        ]
        read_only_fields = ["created_at", "updated_at", "deleted_at"]


class EquipmentRequestSerializer(AuditActorModelSerializer):
    requester_username = serializers.CharField(source="requester.username", read_only=True)
    equipment_name = serializers.CharField(source="equipment.name", read_only=True)
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)
    cabinet_name = serializers.CharField(source="cabinet.name", read_only=True)

    def create(self, validated_data):
        validated_data.setdefault("status", REQUEST_PENDING)
        if not validated_data.get("needed_by"):
            validated_data["needed_by"] = timezone.localdate()
        return super().create(validated_data)

    class Meta:
        model = EquipmentRequest
        fields = [
            "id",
            "requester",
            "requester_username",
            "workplace",
            "workplace_name",
            "cabinet",
            "cabinet_name",
            "equipment",
            "equipment_name",
            "quantity",
            "request_kind",
            "status",
            "requested_at",
            "needed_by",
            "comment",
            "processed_by",
            "processed_at",
            "deleted_at",
        ]
        read_only_fields = ["requester", "processed_by", "processed_at", "deleted_at"]
        extra_kwargs = {
            "request_kind": {"required": False},
            "status": {"required": False},
        }


class MaterialUsageSerializer(AuditActorModelSerializer):
    equipment_name = serializers.CharField(source="equipment.name", read_only=True)
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)
    used_by_username = serializers.CharField(source="used_by.username", read_only=True)

    class Meta:
        model = MaterialUsage
        fields = [
            "id",
            "equipment",
            "equipment_name",
            "workplace",
            "workplace_name",
            "quantity",
            "used_by",
            "used_by_username",
            "used_at",
            "related_request",
            "note",
            "deleted_at",
        ]
        read_only_fields = ["used_by", "deleted_at"]


class InventoryAdjustmentSerializer(AuditActorModelSerializer):
    equipment_name = serializers.CharField(source="equipment.name", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = InventoryAdjustment
        fields = [
            "id",
            "equipment",
            "equipment_name",
            "delta",
            "reason",
            "created_at",
            "created_by",
            "created_by_username",
            "deleted_at",
        ]
        read_only_fields = ["created_by", "deleted_at"]


class EquipmentCheckoutSerializer(AuditActorModelSerializer):
    equipment_name = serializers.CharField(source="equipment.name", read_only=True)
    taken_by_username = serializers.CharField(source="taken_by.username", read_only=True)
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)
    cabinet_code = serializers.CharField(source="cabinet.code", read_only=True)

    class Meta:
        model = EquipmentCheckout
        fields = [
            "id",
            "equipment",
            "equipment_name",
            "taken_by",
            "taken_by_username",
            "workplace",
            "workplace_name",
            "cabinet",
            "cabinet_code",
            "related_request",
            "quantity",
            "taken_at",
            "due_at",
            "returned_at",
            "note",
            "deleted_at",
        ]
        read_only_fields = ["taken_by", "deleted_at"]


class DirectMessageSerializer(AuditActorModelSerializer):
    sender_username = serializers.CharField(source="sender.username", read_only=True)
    recipient_username = serializers.CharField(source="recipient.username", read_only=True)

    class Meta:
        model = DirectMessage
        fields = [
            "id",
            "sender",
            "sender_username",
            "recipient",
            "recipient_username",
            "body",
            "created_at",
            "read_at",
        ]
        read_only_fields = ["sender", "created_at"]


class EquipmentRequestMessageSerializer(AuditActorModelSerializer):
    author_username = serializers.CharField(source="author.username", read_only=True)

    class Meta:
        model = EquipmentRequestMessage
        fields = [
            "id",
            "request",
            "author",
            "author_username",
            "parent",
            "body",
            "created_at",
        ]
        read_only_fields = ["author", "created_at"]


class EquipmentRequestPhotoSerializer(AuditActorModelSerializer):
    uploaded_by_username = serializers.CharField(source="uploaded_by.username", read_only=True)
    image_url = serializers.SerializerMethodField()

    def get_image_url(self, obj):
        request = self.context.get("request")
        if not obj.image:
            return ""
        if request:
            return request.build_absolute_uri(obj.image.url)
        return obj.image.url

    class Meta:
        model = EquipmentRequestPhoto
        fields = [
            "id",
            "request",
            "message",
            "image",
            "image_url",
            "caption",
            "uploaded_by",
            "uploaded_by_username",
            "uploaded_at",
        ]
        read_only_fields = ["uploaded_by", "uploaded_at"]


class PeriodicMaterialUsageScheduleSerializer(AuditActorModelSerializer):
    equipment_name = serializers.CharField(source="equipment.name", read_only=True)
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = PeriodicMaterialUsageSchedule
        fields = [
            "id",
            "title",
            "equipment",
            "equipment_name",
            "workplace",
            "workplace_name",
            "quantity",
            "frequency",
            "next_run_on",
            "is_active",
            "created_by",
            "created_by_username",
            "last_run_at",
            "created_at",
            "deleted_at",
        ]
        read_only_fields = ["created_by", "last_run_at", "created_at", "deleted_at"]


class EmployeeScheduleSerializer(AuditActorModelSerializer):
    user_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = EmployeeSchedule
        fields = [
            "id",
            "user",
            "user_username",
            "schedule_type",
            "cycle_start_date",
            "custom_workdays",
            "is_active",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class UserPreferenceSerializer(AuditActorModelSerializer):
    user_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = UserPreference
        fields = [
            "id",
            "user",
            "user_username",
            "theme_variant",
            "page_size",
            "preferred_language",
            "date_display_format",
            "default_request_status",
            "default_request_kind",
            "default_usage_period_days",
            "default_checkout_status",
            "hotkeys_enabled",
            "show_hotkey_legend",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class RegistrationAllowedEmailDomainSerializer(AuditActorModelSerializer):
    class Meta:
        model = RegistrationAllowedEmailDomain
        fields = ["id", "domain", "is_active", "notes"]

