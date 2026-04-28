from rest_framework import serializers

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import Cabinet, EquipmentCategory, Workplace
from operations.models import EquipmentRequest, MaterialUsage


class AuditActorModelSerializer(serializers.ModelSerializer):
    def create(self, validated_data):
        actor = validated_data.pop("_actor", None)
        instance = self.Meta.model(**validated_data)
        if actor is not None:
            instance._actor = actor
        instance.save()
        return instance

    def update(self, instance, validated_data):
        actor = validated_data.pop("_actor", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if actor is not None:
            instance._actor = actor
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
    cabinet_code = serializers.CharField(source="cabinet.code", read_only=True)

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
            "cabinet",
            "cabinet_code",
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

    class Meta:
        model = EquipmentRequest
        fields = [
            "id",
            "requester",
            "requester_username",
            "workplace",
            "workplace_name",
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


