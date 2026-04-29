from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from inventory.authz import (
    GROUP_ROLE_ADMIN,
    GROUP_FIRST_LINE_SUPPORT,
    GROUP_SENIOR_TECHNICIAN,
    GROUP_SYSADMIN,
    GROUP_TECHNICIAN,
    ROLE_SPECS,
)


class Command(BaseCommand):
    help = "Initialize default role groups and permissions."

    def handle(self, *args, **options):
        groups = {
            GROUP_ROLE_ADMIN: list(ROLE_SPECS[GROUP_ROLE_ADMIN].permissions),
            GROUP_SYSADMIN: None,
            GROUP_SENIOR_TECHNICIAN: list(ROLE_SPECS[GROUP_SENIOR_TECHNICIAN].permissions),
            GROUP_TECHNICIAN: list(ROLE_SPECS[GROUP_TECHNICIAN].permissions),
            GROUP_FIRST_LINE_SUPPORT: list(ROLE_SPECS[GROUP_FIRST_LINE_SUPPORT].permissions),
        }

        app_labels = ["core", "assets", "operations", "audit"]
        all_perms = Permission.objects.filter(content_type__app_label__in=app_labels)

        for group_name, perm_codenames in groups.items():
            group, _ = Group.objects.get_or_create(name=group_name)
            if perm_codenames is None:
                group.permissions.set(all_perms)
            else:
                perms = Permission.objects.filter(codename__in=perm_codenames)
                group.permissions.set(perms)
            group.save()

        self.stdout.write(self.style.SUCCESS("Role groups initialized."))
