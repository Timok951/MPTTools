GROUP_ADMIN = "Administrator"
GROUP_WAREHOUSE = "Warehouse"
GROUP_SYSADMIN = "Sysadmin"
GROUP_BUILDER = "Builder"


def user_in_group(user, group_name: str) -> bool:
    return bool(user and (user.is_superuser or user.groups.filter(name=group_name).exists()))


def is_portal_admin(user) -> bool:
    return bool(user and user.is_authenticated and user_in_group(user, GROUP_ADMIN))
