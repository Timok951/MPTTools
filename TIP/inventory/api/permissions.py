from rest_framework.permissions import BasePermission, SAFE_METHODS

from inventory.authz import GROUP_ADMIN, GROUP_BUILDER, GROUP_FIRST_LINE_SUPPORT, GROUP_SYSADMIN, GROUP_WAREHOUSE, user_in_group

ALL_API_ROLES = (GROUP_ADMIN, GROUP_WAREHOUSE, GROUP_SYSADMIN, GROUP_BUILDER, GROUP_FIRST_LINE_SUPPORT)


def user_has_api_role(user, role_names) -> bool:
    return any(user_in_group(user, group_name) for group_name in role_names)


class CanAccessInventoryApi(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if request.method in SAFE_METHODS and not hasattr(view, "get_api_action"):
            return user_has_api_role(user, ALL_API_ROLES)

        api_action = view.get_api_action() if hasattr(view, "get_api_action") else "read"
        allowed_roles = view.get_allowed_roles(api_action) if hasattr(view, "get_allowed_roles") else ALL_API_ROLES
        return user_has_api_role(user, allowed_roles)

    def has_object_permission(self, request, view, obj):
        if not self.has_permission(request, view):
            return False
        if not hasattr(view, "has_api_object_access"):
            return True
        return view.has_api_object_access(request.user, view.get_api_action(), obj)
