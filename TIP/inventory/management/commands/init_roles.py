from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

GROUP_ADMIN = "Administrator"
GROUP_WAREHOUSE = "Warehouse"
GROUP_SYSADMIN = "Sysadmin"
GROUP_BUILDER = "Builder"


class Command(BaseCommand):
    help = "Initialize default role groups and permissions."

    def handle(self, *args, **options):
        groups = {
            GROUP_ADMIN: None,
            GROUP_WAREHOUSE: [
                "view_equipment",
                "change_equipment",
                "view_equipmentrequest",
                "change_equipmentrequest",
                "add_inventoryadjustment",
                "view_inventoryadjustment",
                "add_materialusage",
                "view_materialusage",
                "view_supplier",
                "view_workplace",
                "view_cabinet",
            ],
            GROUP_SYSADMIN: [
                "view_equipment",
                "view_equipmentrequest",
                "add_equipmentrequest",
                "add_equipmentcheckout",
                "view_equipmentcheckout",
                "view_materialusage",
                "add_worktimer",
                "view_worktimer",
                "view_supplier",
                "view_workplace",
                "view_cabinet",
            ],
            GROUP_BUILDER: [
                "view_equipment",
                "view_equipmentrequest",
                "add_equipmentrequest",
                "add_equipmentcheckout",
                "view_equipmentcheckout",
                "view_materialusage",
                "add_worktimer",
                "view_worktimer",
                "view_supplier",
                "view_workplace",
                "view_cabinet",
            ],
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
