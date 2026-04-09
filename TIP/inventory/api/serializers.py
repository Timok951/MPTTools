from rest_framework import serializers

from assets.models import Equipment, EquipmentCheckout
from core.models import Cabinet, EquipmentCategory, Supplier, Workplace
from operations.models import EquipmentRequest, MaterialUsage, WorkTimer


class WorkplaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workplace
        fields = ["id", "name", "location", "description", "deleted_at"]


class CabinetSerializer(serializers.ModelSerializer):
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)

    class Meta:
        model = Cabinet
        fields = ["id", "code", "name", "floor", "description", "workplace", "workplace_name", "deleted_at"]


class EquipmentCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentCategory
        fields = ["id", "name", "description", "deleted_at"]


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "contact_name", "phone", "email", "address", "notes", "deleted_at"]


class EquipmentSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
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
            "supplier",
            "supplier_name",
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
            "last_inventory_at",
            "inventory_interval_days",
            "notes",
            "created_at",
            "updated_at",
            "deleted_at",
        ]


class EquipmentRequestSerializer(serializers.ModelSerializer):
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


class MaterialUsageSerializer(serializers.ModelSerializer):
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


class EquipmentCheckoutSerializer(serializers.ModelSerializer):
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


class WorkTimerSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source="user.username", read_only=True)
    workplace_name = serializers.CharField(source="workplace.name", read_only=True)
    equipment_name = serializers.CharField(source="equipment.name", read_only=True)

    class Meta:
        model = WorkTimer
        fields = [
            "id",
            "user",
            "user_username",
            "workplace",
            "workplace_name",
            "equipment",
            "equipment_name",
            "started_at",
            "ended_at",
            "note",
            "deleted_at",
        ]
