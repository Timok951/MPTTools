GROUP_SENIOR_TECHNICIAN = "Старший техник"
GROUP_TECHNICIAN = "Техник"
GROUP_SYSADMIN = "Системный администратор"
GROUP_FIRST_LINE_SUPPORT = "Поддержка первой линии"

# Backward-compatible aliases for previous role names.
ROLE_ALIASES = {
    GROUP_SENIOR_TECHNICIAN: {"Warehouse"},
    GROUP_TECHNICIAN: {"Builder"},
    GROUP_SYSADMIN: {"Administrator", "Sysadmin"},
    GROUP_FIRST_LINE_SUPPORT: set(),
}

# Legacy constant names kept to avoid touching every import.
GROUP_ADMIN = GROUP_SYSADMIN
GROUP_WAREHOUSE = GROUP_SENIOR_TECHNICIAN
GROUP_BUILDER = GROUP_TECHNICIAN


def user_in_group(user, group_name: str) -> bool:
    if not user:
        return False
    if user.is_superuser:
        return True
    allowed_names = {group_name}
    allowed_names.update(ROLE_ALIASES.get(group_name, set()))
    return user.groups.filter(name__in=allowed_names).exists()


def is_portal_admin(user) -> bool:
    return bool(user and user.is_authenticated and user_in_group(user, GROUP_ADMIN))
