from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from core.models import Cabinet, EquipmentCategory, Supplier, Workplace
from inventory.authz import GROUP_ADMIN, GROUP_BUILDER, GROUP_SYSADMIN, GROUP_WAREHOUSE, user_in_group
from operations.models import (
    REQUEST_APPROVED,
    REQUEST_KIND_BUILDER,
    REQUEST_KIND_SYSADMIN,
    REQUEST_PENDING,
    EquipmentRequest,
    MaterialUsage,
    WorkTimer,
)

from .permissions import ALL_API_ROLES, CanAccessInventoryApi
from .serializers import (
    CabinetSerializer,
    EquipmentCategorySerializer,
    EquipmentCheckoutSerializer,
    InventoryAdjustmentSerializer,
    EquipmentRequestSerializer,
    EquipmentSerializer,
    MaterialUsageSerializer,
    SupplierSerializer,
    WorkTimerSerializer,
    WorkplaceSerializer,
)


class InventoryModelViewSet(viewsets.ModelViewSet):
    permission_classes = [CanAccessInventoryApi]
    role_matrix = {
        "read": ALL_API_ROLES,
        "create": (GROUP_ADMIN,),
        "update": (GROUP_ADMIN,),
        "delete": (GROUP_ADMIN,),
    }
    privileged_update_roles = ()
    privileged_delete_roles = ()
    privileged_read_roles = ()
    owner_field = None
    owner_read_roles = ()
    owner_update_roles = ()
    owner_delete_roles = ()

    def get_api_action(self):
        if self.action in {"list", "retrieve"}:
            return "read"
        if self.action == "create":
            return "create"
        if self.action in {"update", "partial_update"}:
            return "update"
        if self.action == "destroy":
            return "delete"
        return "read"

    def get_allowed_roles(self, api_action):
        return self.role_matrix.get(api_action, ())

    def get_queryset(self):
        return super().get_queryset()

    def scope_queryset_for_user(self, queryset):
        return queryset

    def filter_queryset_by_role(self, queryset):
        if user_in_group(self.request.user, GROUP_ADMIN) or user_in_group(self.request.user, GROUP_WAREHOUSE):
            return queryset
        return self.scope_queryset_for_user(queryset)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.filter_queryset_by_role(self.get_queryset()))
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def has_api_object_access(self, user, api_action, obj):
        if user_in_group(user, GROUP_ADMIN):
            return True

        privileged_roles = getattr(self, f"privileged_{api_action}_roles", ())
        if any(user_in_group(user, role_name) for role_name in privileged_roles):
            return True

        owner_roles = getattr(self, f"owner_{api_action}_roles", ())
        if self.owner_field and any(user_in_group(user, role_name) for role_name in owner_roles):
            owner = getattr(obj, self.owner_field, None)
            return getattr(owner, "pk", owner) == user.pk

        return False

    def _request_user_has_any_role(self, role_names):
        return any(user_in_group(self.request.user, role_name) for role_name in role_names)

    def _save_with_actor(self, serializer, **extra_kwargs):
        return serializer.save(_actor=self.request.user, **extra_kwargs)

    def perform_destroy(self, instance):
        instance._actor = self.request.user
        instance.delete()


class EquipmentViewSet(InventoryModelViewSet):
    serializer_class = EquipmentSerializer
    role_matrix = {
        "read": ALL_API_ROLES,
        "create": (GROUP_ADMIN,),
        "update": (GROUP_ADMIN, GROUP_WAREHOUSE),
        "delete": (GROUP_ADMIN,),
    }
    privileged_update_roles = (GROUP_ADMIN, GROUP_WAREHOUSE)
    warehouse_editable_fields = {
        "status",
        "quantity_total",
        "quantity_available",
        "low_stock_threshold",
        "workplace",
        "cabinet",
        "last_inventory_at",
        "inventory_interval_days",
        "notes",
    }

    def get_queryset(self):
        return Equipment.objects.select_related("category", "supplier", "workplace", "cabinet").order_by("name", "inventory_number")

    def perform_create(self, serializer):
        self._save_with_actor(serializer)

    def perform_update(self, serializer):
        changed_fields = set(serializer.validated_data.keys())
        if user_in_group(self.request.user, GROUP_WAREHOUSE) and not user_in_group(self.request.user, GROUP_ADMIN):
            disallowed_fields = changed_fields - self.warehouse_editable_fields
            if disallowed_fields:
                raise PermissionDenied(
                    f"Warehouse role can only update stock and placement fields via API: {', '.join(sorted(disallowed_fields))}."
                )
        self._save_with_actor(serializer)


class WorkplaceViewSet(InventoryModelViewSet):
    queryset = Workplace.objects.order_by("name")
    serializer_class = WorkplaceSerializer


class CabinetViewSet(InventoryModelViewSet):
    queryset = Cabinet.objects.select_related("workplace").order_by("code")
    serializer_class = CabinetSerializer


class EquipmentCategoryViewSet(InventoryModelViewSet):
    queryset = EquipmentCategory.objects.order_by("name")
    serializer_class = EquipmentCategorySerializer


class SupplierViewSet(InventoryModelViewSet):
    queryset = Supplier.objects.order_by("name")
    serializer_class = SupplierSerializer


class EquipmentRequestViewSet(InventoryModelViewSet):
    queryset = EquipmentRequest.objects.select_related("requester", "workplace", "equipment", "processed_by").order_by("-requested_at")
    serializer_class = EquipmentRequestSerializer
    role_matrix = {
        "read": ALL_API_ROLES,
        "create": (GROUP_ADMIN, GROUP_SYSADMIN, GROUP_BUILDER),
        "update": (GROUP_ADMIN, GROUP_WAREHOUSE, GROUP_SYSADMIN, GROUP_BUILDER),
        "delete": (GROUP_ADMIN,),
    }
    privileged_read_roles = (GROUP_WAREHOUSE,)
    privileged_update_roles = (GROUP_ADMIN, GROUP_WAREHOUSE)
    owner_field = "requester"
    owner_read_roles = (GROUP_SYSADMIN, GROUP_BUILDER)
    owner_update_roles = (GROUP_SYSADMIN, GROUP_BUILDER)
    owner_editable_fields = {"workplace", "equipment", "quantity", "needed_by", "comment"}

    def has_api_object_access(self, user, api_action, obj):
        if not super().has_api_object_access(user, api_action, obj):
            return False
        if api_action == "update" and obj.requester_id == user.pk and not user_in_group(user, GROUP_WAREHOUSE):
            return obj.status == REQUEST_PENDING
        return True

    def _request_kind_for_user(self):
        if user_in_group(self.request.user, GROUP_ADMIN) or user_in_group(self.request.user, GROUP_WAREHOUSE):
            return None
        if user_in_group(self.request.user, GROUP_BUILDER):
            return REQUEST_KIND_BUILDER
        if user_in_group(self.request.user, GROUP_SYSADMIN):
            return REQUEST_KIND_SYSADMIN
        return None

    def scope_queryset_for_user(self, queryset):
        return queryset.filter(requester=self.request.user)

    def perform_create(self, serializer):
        extra_kwargs = {"requester": self.request.user}
        request_kind = self._request_kind_for_user()
        if request_kind:
            extra_kwargs["request_kind"] = request_kind
        self._save_with_actor(serializer, **extra_kwargs)

    def perform_update(self, serializer):
        extra_kwargs = {}
        changed_fields = set(serializer.validated_data.keys())
        if self.request.user == serializer.instance.requester and not self._request_user_has_any_role((GROUP_ADMIN, GROUP_WAREHOUSE)):
            disallowed_fields = changed_fields - self.owner_editable_fields
            if disallowed_fields:
                raise PermissionDenied(
                    f"You can only update your own pending request details via API: {', '.join(sorted(disallowed_fields))}."
                )
            request_kind = self._request_kind_for_user()
            if request_kind:
                extra_kwargs["request_kind"] = request_kind
        elif "status" in changed_fields:
            extra_kwargs["processed_by"] = self.request.user
            extra_kwargs["processed_at"] = timezone.now()
        self._save_with_actor(serializer, **extra_kwargs)


class MaterialUsageViewSet(InventoryModelViewSet):
    queryset = MaterialUsage.objects.select_related("equipment", "workplace", "used_by", "related_request").order_by("-used_at")
    serializer_class = MaterialUsageSerializer
    role_matrix = {
        "read": ALL_API_ROLES,
        "create": ALL_API_ROLES,
        "update": ALL_API_ROLES,
        "delete": (GROUP_ADMIN,),
    }
    privileged_read_roles = (GROUP_WAREHOUSE,)
    privileged_update_roles = (GROUP_ADMIN, GROUP_WAREHOUSE)
    owner_field = "used_by"
    owner_read_roles = (GROUP_SYSADMIN, GROUP_BUILDER)
    owner_update_roles = (GROUP_SYSADMIN, GROUP_BUILDER)

    def scope_queryset_for_user(self, queryset):
        return queryset.filter(used_by=self.request.user)

    def perform_create(self, serializer):
        self._save_with_actor(serializer, used_by=self.request.user)

    def perform_update(self, serializer):
        self._save_with_actor(serializer)


class InventoryAdjustmentViewSet(InventoryModelViewSet):
    queryset = InventoryAdjustment.objects.select_related("equipment", "created_by").order_by("-created_at")
    serializer_class = InventoryAdjustmentSerializer
    role_matrix = {
        "read": ALL_API_ROLES,
        "create": (GROUP_ADMIN, GROUP_WAREHOUSE),
        "update": (GROUP_ADMIN, GROUP_WAREHOUSE),
        "delete": (GROUP_ADMIN,),
    }
    privileged_update_roles = (GROUP_ADMIN, GROUP_WAREHOUSE)
    privileged_delete_roles = (GROUP_ADMIN,)

    def _apply_adjustment_delta(self, equipment_id, delta):
        equipment = Equipment.objects.get(pk=equipment_id)
        if equipment.quantity_total + delta < 0 or equipment.quantity_available + delta < 0:
            raise ValidationError("Adjustment change would make stock negative.")
        Equipment.objects.filter(pk=equipment_id).update(
            quantity_total=F("quantity_total") + delta,
            quantity_available=F("quantity_available") + delta,
        )

    def perform_create(self, serializer):
        with transaction.atomic():
            self._save_with_actor(serializer, created_by=self.request.user)

    def perform_update(self, serializer):
        with transaction.atomic():
            previous = serializer.instance
            previous_equipment_id = previous.equipment_id
            previous_delta = previous.delta
            instance = self._save_with_actor(serializer)

            if previous_equipment_id == instance.equipment_id:
                diff = instance.delta - previous_delta
                if diff:
                    self._apply_adjustment_delta(instance.equipment_id, diff)
                return

            if previous_equipment_id:
                self._apply_adjustment_delta(previous_equipment_id, -previous_delta)
            if instance.equipment_id:
                self._apply_adjustment_delta(instance.equipment_id, instance.delta)

    def perform_destroy(self, instance):
        with transaction.atomic():
            if instance.equipment_id:
                self._apply_adjustment_delta(instance.equipment_id, -instance.delta)
            super().perform_destroy(instance)


class EquipmentCheckoutViewSet(InventoryModelViewSet):
    queryset = EquipmentCheckout.objects.select_related("equipment", "taken_by", "workplace", "cabinet", "related_request").order_by("-taken_at")
    serializer_class = EquipmentCheckoutSerializer
    role_matrix = {
        "read": ALL_API_ROLES,
        "create": (GROUP_ADMIN, GROUP_SYSADMIN, GROUP_BUILDER),
        "update": (GROUP_ADMIN, GROUP_SYSADMIN, GROUP_BUILDER),
        "delete": (GROUP_ADMIN,),
    }
    privileged_read_roles = (GROUP_WAREHOUSE,)
    privileged_update_roles = (GROUP_ADMIN,)
    owner_field = "taken_by"
    owner_read_roles = (GROUP_SYSADMIN, GROUP_BUILDER)
    owner_update_roles = (GROUP_SYSADMIN, GROUP_BUILDER)
    editable_fields = {"workplace", "cabinet", "due_at", "returned_at", "note"}

    def scope_queryset_for_user(self, queryset):
        return queryset.filter(taken_by=self.request.user)

    def perform_create(self, serializer):
        with transaction.atomic():
            self._save_with_actor(serializer, taken_by=self.request.user)

    def perform_update(self, serializer):
        changed_fields = set(serializer.validated_data.keys())
        disallowed_fields = changed_fields - self.editable_fields
        if disallowed_fields:
            raise PermissionDenied(
                f"Existing checkout updates are limited to return and note fields via API: {', '.join(sorted(disallowed_fields))}."
            )
        if serializer.instance.returned_at and "returned_at" in serializer.validated_data and serializer.validated_data["returned_at"] is None:
            raise PermissionDenied("Returned checkouts cannot be reopened via API.")
        with transaction.atomic():
            self._save_with_actor(serializer)

    def perform_destroy(self, instance):
        with transaction.atomic():
            if instance.equipment_id and not instance.returned_at:
                Equipment.objects.filter(pk=instance.equipment_id).update(
                    quantity_available=F("quantity_available") + instance.quantity
                )
            if instance.related_request_id and not instance.returned_at:
                EquipmentRequest.objects.filter(pk=instance.related_request_id).update(status=REQUEST_APPROVED)
            super().perform_destroy(instance)


class WorkTimerViewSet(InventoryModelViewSet):
    queryset = WorkTimer.objects.select_related("user", "workplace", "equipment").order_by("-started_at")
    serializer_class = WorkTimerSerializer
    role_matrix = {
        "read": ALL_API_ROLES,
        "create": (GROUP_ADMIN, GROUP_SYSADMIN, GROUP_BUILDER),
        "update": (GROUP_ADMIN, GROUP_SYSADMIN, GROUP_BUILDER),
        "delete": (GROUP_ADMIN,),
    }
    privileged_read_roles = (GROUP_WAREHOUSE,)
    privileged_update_roles = (GROUP_ADMIN,)
    owner_field = "user"
    owner_read_roles = (GROUP_SYSADMIN, GROUP_BUILDER)
    owner_update_roles = (GROUP_SYSADMIN, GROUP_BUILDER)

    def scope_queryset_for_user(self, queryset):
        return queryset.filter(user=self.request.user)

    def perform_create(self, serializer):
        self._save_with_actor(serializer, user=self.request.user)

    def perform_update(self, serializer):
        self._save_with_actor(serializer)
