from dataclasses import dataclass


GROUP_ROLE_ADMIN = "Администратор"
GROUP_SENIOR_TECHNICIAN = "Старший техник"
GROUP_TECHNICIAN = "Техник"
GROUP_SYSADMIN = "Системный администратор"
GROUP_FIRST_LINE_SUPPORT = "Поддержка первой линии"

# Backward-compatible aliases for previous role names.
ROLE_ALIASES = {
    GROUP_ROLE_ADMIN: set(),
    GROUP_SENIOR_TECHNICIAN: {"Warehouse"},
    GROUP_TECHNICIAN: {"Builder"},
    GROUP_SYSADMIN: {"Sysadmin"},
    GROUP_FIRST_LINE_SUPPORT: set(),
}


@dataclass(frozen=True)
class RoleSpec:
    slug: str
    title: str
    description: str
    permissions: tuple[str, ...]
    capabilities: tuple[str, ...]


ROLE_CAPABILITY_LABELS = {
    "warehouse_operations": "Склад: оборудование, остатки, корректировки",
    "request_creation": "Создание заявок на оборудование",
    "request_processing": "Обработка и смена статусов заявок",
    "checkout_operations": "Выдача и просмотр выдач",
    "usage_writeoff": "Списание оборудования",
    "report_access": "Просмотр отчётов и аналитики",
    "users_and_site_admin": "Управление пользователями, сайтом, кабинетами",
    "quality_access": "Просмотр и обновление отчёта качества",
    "data_tools_access": "Инструменты данных и резервные копии",
}

ROLE_SPECS: dict[str, RoleSpec] = {
    GROUP_ROLE_ADMIN: RoleSpec(
        slug="administrator",
        title=GROUP_ROLE_ADMIN,
        description="Администрирование ролей пользователей без складских и операционных действий.",
        permissions=(),
        capabilities=(
            "users_and_site_admin",
        ),
    ),
    GROUP_SYSADMIN: RoleSpec(
        slug="sysadmin",
        title=GROUP_SYSADMIN,
        description="Полный административный доступ к пользователям, сайту и кабинетам.",
        permissions=(),
        capabilities=(
            "warehouse_operations",
            "request_creation",
            "request_processing",
            "checkout_operations",
            "usage_writeoff",
            "report_access",
            "users_and_site_admin",
            "quality_access",
            "data_tools_access",
        ),
    ),
    GROUP_SENIOR_TECHNICIAN: RoleSpec(
        slug="senior_technician",
        title=GROUP_SENIOR_TECHNICIAN,
        description="Работа со складом, выдачей оборудования и операционной отчётностью.",
        permissions=(
            "view_equipment",
            "change_equipment",
            "view_equipmentrequest",
            "change_equipmentrequest",
            "add_equipmentcheckout",
            "view_equipmentcheckout",
            "add_inventoryadjustment",
            "view_inventoryadjustment",
            "add_materialusage",
            "view_materialusage",
            "view_workplace",
            "view_cabinet",
        ),
        capabilities=(
            "warehouse_operations",
            "request_processing",
            "checkout_operations",
            "usage_writeoff",
            "report_access",
        ),
    ),
    GROUP_TECHNICIAN: RoleSpec(
        slug="technician",
        title=GROUP_TECHNICIAN,
        description="Создание заявок без прав на обработку и выдачу оборудования.",
        permissions=(
            "view_equipment",
            "view_equipmentrequest",
            "add_equipmentrequest",
            "view_workplace",
            "view_cabinet",
        ),
        capabilities=(
            "request_creation",
        ),
    ),
    GROUP_FIRST_LINE_SUPPORT: RoleSpec(
        slug="first_line_support",
        title=GROUP_FIRST_LINE_SUPPORT,
        description="Быстрая обработка и сопровождение заявок пользователей.",
        permissions=(
            "view_equipmentrequest",
            "change_equipmentrequest",
            "view_equipment",
        ),
        capabilities=(
            "request_processing",
        ),
    ),
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


def user_capabilities(user) -> set[str]:
    if not user:
        return set()
    if getattr(user, "is_superuser", False):
        return set(ROLE_CAPABILITY_LABELS.keys())
    caps = set()
    for role_name, spec in ROLE_SPECS.items():
        if user_in_group(user, role_name):
            caps.update(spec.capabilities)
    return caps


def user_has_capability(user, capability: str) -> bool:
    return capability in user_capabilities(user)


def is_portal_admin(user) -> bool:
    return bool(user and user.is_authenticated and user_in_group(user, GROUP_ADMIN))
