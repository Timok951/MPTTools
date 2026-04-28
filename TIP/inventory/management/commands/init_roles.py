from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

GROUP_SENIOR_TECHNICIAN = "Старший техник"
GROUP_TECHNICIAN = "Техник"
GROUP_SYSADMIN = "Системный администратор"
GROUP_FIRST_LINE_SUPPORT = "Поддержка первой линии"


class Command(BaseCommand):
    help = "Initialize default role groups and permissions."

    def handle(self, *args, **options):
        groups = {
            GROUP_SYSADMIN: None,
            GROUP_SENIOR_TECHNICIAN: [
                "view_equipment",
                "change_equipment",
                "view_equipmentrequest",
                "change_equipmentrequest",
                "add_inventoryadjustment",
                "view_inventoryadjustment",
                "add_materialusage",
                "view_materialusage",
                "view_workplace",
                "view_cabinet",
            ],
            GROUP_TECHNICIAN: [
                "view_equipment",
                "view_equipmentrequest",
                "add_equipmentrequest",
                "add_equipmentcheckout",
                "view_equipmentcheckout",
                "view_workplace",
                "view_cabinet",
            ],
            GROUP_FIRST_LINE_SUPPORT: [
                "view_equipmentrequest",
                "change_equipmentrequest",
                "view_equipment",
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
