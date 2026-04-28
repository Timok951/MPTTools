from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from core.models import Workplace
from inventory.authz import (
    GROUP_FIRST_LINE_SUPPORT,
    GROUP_SYSADMIN,
    GROUP_TECHNICIAN,
)
from operations.models import (
    EquipmentRequest,
    EquipmentRequestMessage,
    REQUEST_APPROVED,
    REQUEST_PENDING,
)


class RoleAssignmentUiTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.admin = User.objects.create_user(username="admin_roles", password=self.password)
        self.tech = User.objects.create_user(
            username="tech_user",
            email="tech@example.com",
            first_name="Ivan",
            last_name="Petrov",
            password=self.password,
        )
        self.support = User.objects.create_user(username="support_user", password=self.password)
        self.no_role = User.objects.create_user(username="new_user", password=self.password)

        admin_group, _ = Group.objects.get_or_create(name=GROUP_SYSADMIN)
        technician_group, _ = Group.objects.get_or_create(name=GROUP_TECHNICIAN)
        support_group, _ = Group.objects.get_or_create(name=GROUP_FIRST_LINE_SUPPORT)

        self.admin.groups.add(admin_group)
        self.tech.groups.add(technician_group)
        self.support.groups.add(support_group)

    def test_role_assignment_filters_users(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("role_assignment"), {"q": "ivan", "role": GROUP_TECHNICIAN})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tech_user")
        self.assertNotContains(response, "support_user")
        self.assertNotContains(response, "new_user")

    def test_role_assignment_can_clear_role(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("role_assignment"),
            {
                "user_id": self.tech.pk,
                "role": "",
                "q": "",
                "role_filter": GROUP_TECHNICIAN,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.tech.refresh_from_db()
        self.assertFalse(self.tech.groups.exists())


class RequestStatusUxTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.support = User.objects.create_user(username="line_support", password=self.password)
        self.requester = User.objects.create_user(username="requester", password=self.password)
        support_group, _ = Group.objects.get_or_create(name=GROUP_FIRST_LINE_SUPPORT)
        requester_group, _ = Group.objects.get_or_create(name=GROUP_TECHNICIAN)
        self.support.groups.add(support_group)
        self.requester.groups.add(requester_group)
        self.workplace = Workplace.objects.create(name="Main room")
        self.request_item = EquipmentRequest.objects.create(
            requester=self.requester,
            workplace=self.workplace,
            quantity=2,
            request_kind="builder",
            status=REQUEST_PENDING,
            comment="Need hardware",
        )

    def test_status_update_creates_service_message_with_note(self):
        self.client.force_login(self.support)

        response = self.client.post(
            reverse("request_update_status", args=[self.request_item.pk]),
            {"status": REQUEST_APPROVED, "status_note": "Готово к выдаче."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.request_item.refresh_from_db()
        self.assertEqual(self.request_item.status, REQUEST_APPROVED)
        self.assertEqual(self.request_item.processed_by, self.support)
        status_message = EquipmentRequestMessage.objects.latest("id")
        self.assertIn("Статус изменён", status_message.body)
        self.assertIn("Готово к выдаче.", status_message.body)

    def test_request_detail_shows_contextual_quick_actions(self):
        self.request_item.status = REQUEST_APPROVED
        self.request_item.save(update_fields=["status"])
        self.client.force_login(self.support)

        response = self.client.get(reverse("request_detail", args=[self.request_item.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Отметить как выданную")
        self.assertContains(response, "Закрыть")
        self.assertNotContains(response, "Вернуть на рассмотрение")
