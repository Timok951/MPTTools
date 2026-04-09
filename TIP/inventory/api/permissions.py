from rest_framework.permissions import BasePermission, SAFE_METHODS

from inventory.authz import GROUP_ADMIN, GROUP_BUILDER, GROUP_SYSADMIN, GROUP_WAREHOUSE, user_in_group


class CanAccessInventoryApi(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return any(
                user_in_group(user, group_name)
                for group_name in (GROUP_ADMIN, GROUP_WAREHOUSE, GROUP_SYSADMIN, GROUP_BUILDER)
            )
        return user_in_group(user, GROUP_ADMIN)
