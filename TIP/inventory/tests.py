import os
import tempfile
from datetime import timedelta
from pathlib import Path
from time import perf_counter
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment, STATUS_IN_STOCK, STATUS_REPAIR, STATUS_RETIRED
from audit.models import AuditLog
from audit.models import AdminPortalLog
from core.models import DirectMessage, EquipmentCategory, PasswordResetCode, UserPreference, Workplace
from inventory.backup_utils import PostgreSQLBackupConfig, create_postgresql_backup, get_postgresql_backup_config
from inventory.authz import GROUP_FIRST_LINE_SUPPORT
from inventory.notification_utils import unread_request_message_count
from inventory.portal_forms import PortalUserForm
from operations.models import (
    REQUEST_APPROVED,
    REQUEST_KIND_BUILDER,
    REQUEST_KIND_WRITEOFF,
    REQUEST_PENDING,
    EquipmentRequest,
    EquipmentRequestMessage,
    MaterialUsage,
)


class TimerAndPreferenceViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="builder", password="secret123")
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.user.groups.add(builder_group)
        self.workplace = Workplace.objects.create(name="Lab 101")

    def test_preferences_view_persists_user_settings(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("user_preferences"),
            {
                "email": "builder_prefs@example.com",
                "theme_variant": "contrast",
                "preferred_language": "ru",
                "page_size": 50,
                "date_display_format": "iso",
                "default_request_status": "pending",
                "default_request_kind": "builder",
                "default_usage_period_days": 14,
                "hotkeys_enabled": "on",
                "show_hotkey_legend": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "builder_prefs@example.com")
        pref = UserPreference.objects.get(user=self.user)
        self.assertEqual(pref.theme_variant, "contrast")
        self.assertEqual(pref.preferred_language, "ru")
        self.assertEqual(pref.page_size, 50)
        self.assertEqual(pref.date_display_format, "iso")
        self.assertEqual(pref.default_request_status, "pending")
        self.assertEqual(pref.default_request_kind, "builder")
        self.assertEqual(pref.default_usage_period_days, 14)
        self.assertTrue(pref.hotkeys_enabled)
        self.assertTrue(pref.show_hotkey_legend)

    def test_preferences_rejects_duplicate_email(self):
        User.objects.create_user(username="other_builder", email="taken@example.com", password="secret123")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("user_preferences"),
            {
                "email": "taken@example.com",
                "theme_variant": "default",
                "preferred_language": "ru",
                "page_size": 25,
                "date_display_format": "compact",
                "default_request_status": "",
                "default_request_kind": "",
                "default_usage_period_days": 30,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже привязан")
        self.user.refresh_from_db()
        self.assertEqual((self.user.email or "").strip(), "")

    def test_preferences_page_renders_dark_theme_option(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("user_preferences"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="dark"')
        self.assertContains(response, "Язык интерфейса")

    def test_saved_language_is_applied_on_next_request(self):
        UserPreference.objects.create(user=self.user, preferred_language="en")
        self.client.force_login(self.user)

        response = self.client.get(reverse("user_preferences"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.LANGUAGE_CODE, "en")
        self.assertContains(response, "User preferences")


class EquipmentCardViewTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.user = User.objects.create_user(username="equipment_user", password=self.password)
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.user.groups.add(builder_group)
        self.workplace = Workplace.objects.create(name="Card workshop")
        self.category = EquipmentCategory.objects.create(name="Tools")

    def test_equipment_list_renders_card_layout_with_photo_support(self):
        Equipment.objects.create(
            name="Drill",
            inventory_number="INV-001",
            category=self.category,
            workplace=self.workplace,
            quantity_total=3,
            quantity_available=2,
            photo="equipment/drill.jpg",
        )
        Equipment.objects.create(
            name="Hammer",
            inventory_number="INV-002",
            category=self.category,
            workplace=self.workplace,
            quantity_total=1,
            quantity_available=1,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("equipment_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="equipment-card-grid"', html=False)
        self.assertContains(response, "/media/equipment/drill.jpg")
        self.assertContains(response, "Нет фото")


class PrometheusMetricsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="metrics_user", password="secret123")
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.user.groups.add(builder_group)

    def test_metrics_endpoint_returns_prometheus_text(self):
        response = self.client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
        body = response.content.decode("utf-8")
        self.assertIn("django_http", body)


class EquipmentQRCodeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="qr_user", password="secret123")
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.user.groups.add(builder_group)
        self.workplace = Workplace.objects.create(name="QR Lab")
        self.category = EquipmentCategory.objects.create(name="QR Tools")
        self.equipment = Equipment.objects.create(
            name="Multimeter",
            inventory_number="QR-001",
            category=self.category,
            workplace=self.workplace,
            quantity_total=1,
            quantity_available=1,
        )

    def test_qr_endpoint_returns_png_image(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("equipment_qr", args=[self.equipment.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(len(response.content) > 100)

    def test_qr_endpoint_requires_authentication(self):
        response = self.client.get(reverse("equipment_qr", args=[self.equipment.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_qr_endpoint_returns_404_for_missing_equipment(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("equipment_qr", args=[999999]))

        self.assertEqual(response.status_code, 404)

    def test_public_equipment_card_is_accessible_without_auth(self):
        response = self.client.get(reverse("equipment_public_card", args=[self.equipment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Публичная карточка")
        self.assertContains(response, self.equipment.name)

    def test_equipment_list_contains_qr_link(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("equipment_list"))

        self.assertEqual(response.status_code, 200)
        expected_url = reverse("equipment_qr", args=[self.equipment.pk])
        self.assertContains(response, expected_url)
        self.assertContains(response, "qr-toggle-btn")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PUBLIC_SITE_URL="https://example.test",
)
class DirectMessageTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.sender = User.objects.create_user(username="sender_user", password=self.password)
        self.recipient = User.objects.create_user(username="recipient_user", password=self.password)
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.sender.groups.add(builder_group)
        self.recipient.groups.add(builder_group)

    def test_authenticated_user_can_open_messages_page(self):
        self.client.force_login(self.sender)

        response = self.client.get(reverse("direct_messages"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сообщения пользователям")

    def test_user_can_send_direct_message(self):
        from django.core import mail

        self.recipient.email = "recipient@mpt.ru"
        self.recipient.save(update_fields=["email"])
        self.client.force_login(self.sender)

        response = self.client.post(
            reverse("direct_messages"),
            {"recipient": self.recipient.pk, "body": "Привет, проверь оборудование."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        message = DirectMessage.objects.get()
        self.assertEqual(message.sender, self.sender)
        self.assertEqual(message.recipient, self.recipient)
        self.assertEqual(message.body, "Привет, проверь оборудование.")
        self.assertContains(response, self.recipient.username)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("recipient@mpt.ru", mail.outbox[0].to)
        self.assertIn("Привет", mail.outbox[0].body)

    def test_opening_dialog_marks_received_messages_as_read(self):
        message = DirectMessage.objects.create(
            sender=self.sender,
            recipient=self.recipient,
            body="Есть новое сообщение",
        )
        self.client.force_login(self.recipient)

        response = self.client.get(f"{reverse('direct_messages')}?user={self.sender.pk}")

        self.assertEqual(response.status_code, 200)
        message.refresh_from_db()
        self.assertIsNotNone(message.read_at)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordResetFlowTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.user = User.objects.create_user(
            username="mail_user",
            email="mail_user@mpt.ru",
            password=self.password,
        )

    def test_request_form_sends_code_and_creates_reset_entry(self):
        response = self.client.post(
            reverse("password_reset_request"),
            {"email": "mail_user@mpt.ru"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PasswordResetCode.objects.count(), 1)
        self.assertContains(response, "Подтверждение кода")

    def test_request_form_puts_multipart_email_in_outbox(self):
        from django.core import mail

        with mock.patch("inventory.views._generate_password_reset_code", return_value="445566"):
            self.client.post(reverse("password_reset_request"), {"email": "mail_user@mpt.ru"}, follow=True)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["mail_user@mpt.ru"])
        self.assertIn("445566", sent.body)
        html_bodies = [alt[0] for alt in sent.alternatives if alt[1] == "text/html"]
        self.assertTrue(html_bodies)
        self.assertIn("445566", html_bodies[0])

    def test_confirm_form_updates_password_from_valid_code(self):
        with mock.patch("inventory.views._generate_password_reset_code", return_value="123456"):
            self.client.post(reverse("password_reset_request"), {"email": "mail_user@mpt.ru"})

        response = self.client.post(
            reverse("password_reset_confirm"),
            {
                "email": "mail_user@mpt.ru",
                "code": "123456",
                "new_password1": "new-secret-123",
                "new_password2": "new-secret-123",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пароль обновлён")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("new-secret-123"))
        self.assertIsNotNone(PasswordResetCode.objects.get().used_at)

    def test_confirm_form_rejects_invalid_code(self):
        with mock.patch("inventory.views._generate_password_reset_code", return_value="123456"):
            self.client.post(reverse("password_reset_request"), {"email": "mail_user@mpt.ru"})

        response = self.client.post(
            reverse("password_reset_confirm"),
            {
                "email": "mail_user@mpt.ru",
                "code": "654321",
                "new_password1": "new-secret-123",
                "new_password2": "new-secret-123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный или просроченный код")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_authenticated_user_without_email_saves_email_on_request(self):
        user = User.objects.create_user(username="no_mail_user", password=self.password)
        self.assertEqual((user.email or "").strip(), "")
        self.client.force_login(user)
        with mock.patch("inventory.views._generate_password_reset_code", return_value="123456"):
            self.client.post(reverse("password_reset_request"), {"email": "newmail@mpt.ru"}, follow=True)
        user.refresh_from_db()
        self.assertEqual(user.email.lower(), "newmail@mpt.ru")

    def test_authenticated_user_without_email_rejects_taken_email(self):
        User.objects.create_user(username="taken_owner", email="taken@mpt.ru", password=self.password)
        user = User.objects.create_user(username="no_mail_two", password=self.password)
        self.client.force_login(user)
        response = self.client.post(reverse("password_reset_request"), {"email": "taken@mpt.ru"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "уже используется")

    def test_authenticated_user_with_email_rejects_other_email(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("password_reset_request"), {"email": "other@mpt.ru"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "профиле")

    def test_authenticated_confirm_rejects_code_for_other_account(self):
        other = User.objects.create_user(username="other_mail_user", email="other@mpt.ru", password="pass-other-9")
        with mock.patch("inventory.views._generate_password_reset_code", return_value="999888"):
            self.client.post(reverse("password_reset_request"), {"email": "mail_user@mpt.ru"})
        self.client.force_login(other)
        response = self.client.post(
            reverse("password_reset_confirm"),
            {
                "email": "mail_user@mpt.ru",
                "code": "999888",
                "new_password1": "new-secret-999",
                "new_password2": "new-secret-999",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "профиле")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_password_reset_request_rejects_non_corporate_email(self):
        response = self.client.post(reverse("password_reset_request"), {"email": "outsider@gmail.com"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "доменах")
        self.assertContains(response, "mpt.ru")
        self.assertEqual(PasswordResetCode.objects.count(), 0)


class RegistrationEmailDomainTests(TestCase):
    def test_register_accepts_mpt_ru_email(self):
        pwd = "Reg-Test-9x!"
        response = self.client.post(
            reverse("register"),
            {
                "username": "new_reg_user",
                "email": "colleague@mpt.ru",
                "password1": pwd,
                "password2": pwd,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))
        user = User.objects.get(username="new_reg_user")
        self.assertEqual(user.email.lower(), "colleague@mpt.ru")

    def test_register_rejects_non_corporate_email(self):
        pwd = "Reg-Test-9y!"
        response = self.client.post(
            reverse("register"),
            {
                "username": "bad_reg_user",
                "email": "person@gmail.com",
                "password1": pwd,
                "password2": pwd,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "доменах")
        self.assertContains(response, "mpt.ru")
        self.assertFalse(User.objects.filter(username="bad_reg_user").exists())

    def test_register_accepts_configured_extra_domain(self):
        from core.models import RegistrationAllowedEmailDomain

        RegistrationAllowedEmailDomain.objects.create(domain="partner.example", is_active=True)
        pwd = "Reg-Test-9z!"
        response = self.client.post(
            reverse("register"),
            {
                "username": "partner_reg_user",
                "email": "person@partner.example",
                "password1": pwd,
                "password2": pwd,
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="partner_reg_user")
        self.assertEqual(user.email.lower(), "person@partner.example")

    def test_register_rejects_domain_when_marked_inactive(self):
        from core.models import RegistrationAllowedEmailDomain

        RegistrationAllowedEmailDomain.objects.create(domain="other.org", is_active=True)
        RegistrationAllowedEmailDomain.objects.filter(domain="mpt.ru").update(is_active=False)
        pwd = "Reg-Test-9w!"
        response = self.client.post(
            reverse("register"),
            {
                "username": "inactive_dom_user",
                "email": "x@mpt.ru",
                "password1": pwd,
                "password2": pwd,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "доменах")
        self.assertFalse(User.objects.filter(username="inactive_dom_user").exists())


class LightweightPerformanceTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.user = User.objects.create_user(username="perf_admin", password=self.password)
        admin_group, _ = Group.objects.get_or_create(name="Administrator")
        self.user.groups.add(admin_group)
        self.workplace = Workplace.objects.create(name="Perf Lab")

    def test_analytics_dashboard_loads_quickly_for_small_fixture(self):
        self.client.force_login(self.user)
        started = perf_counter()
        response = self.client.get(reverse("analytics"))
        elapsed = perf_counter() - started
        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 2.0)


class AdminProcedureTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.admin = User.objects.create_user(username="portal_admin", password=self.password)
        admin_group, _ = Group.objects.get_or_create(name="Administrator")
        self.admin.groups.add(admin_group)

        self.builder = User.objects.create_user(username="portal_builder", password=self.password)
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.builder.groups.add(builder_group)
        self.warehouse = User.objects.create_user(username="portal_warehouse", password=self.password)
        warehouse_group, _ = Group.objects.get_or_create(name="Warehouse")
        self.warehouse.groups.add(warehouse_group)
        self.sysadmin = User.objects.create_user(username="portal_sysadmin", password=self.password)
        sysadmin_group, _ = Group.objects.get_or_create(name="Sysadmin")
        self.sysadmin.groups.add(sysadmin_group)

        self.workplace = Workplace.objects.create(name="Procedure workshop")
        self.category = EquipmentCategory.objects.create(name="Consumables")
        self.equipment = Equipment.objects.create(
            name="Cable ties",
            inventory_number="CONS-001",
            category=self.category,
            workplace=self.workplace,
            is_consumable=True,
            quantity_total=10,
            quantity_available=2,
            low_stock_threshold=5,
        )
        self.non_consumable = Equipment.objects.create(
            name="Drill old",
            inventory_number="NC-001",
            category=self.category,
            workplace=self.workplace,
            is_consumable=False,
            status=STATUS_RETIRED,
            quantity_total=1,
            quantity_available=0,
        )
        self.old_request = EquipmentRequest.objects.create(
            requester=self.builder,
            workplace=self.workplace,
            equipment=self.equipment,
            quantity=1,
            request_kind=REQUEST_KIND_BUILDER,
            requested_at=timezone.now() - timedelta(days=20),
        )

    def test_admin_portal_shows_procedures(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("portal_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Бизнес-процедуры")
        self.assertContains(response, "Запустить процедуру")

    def test_registration_domains_portal_list_loads_for_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("portal_list", kwargs={"entity": "registration-domains"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Домены почты для регистрации")

    def test_non_admin_cannot_run_procedure(self):
        self.client.force_login(self.builder)

        response = self.client.post(reverse("portal_procedure_run", kwargs={"slug": "reject_stale_requests"}), {"reject-stale_days": 14})

        self.assertEqual(response.status_code, 403)

    def test_reject_stale_requests_procedure_updates_requests_and_logs(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("portal_procedure_run", kwargs={"slug": "reject_stale_requests"}),
            {"reject-stale_days": 14},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.old_request.refresh_from_db()
        self.assertEqual(self.old_request.status, "rejected")
        self.assertEqual(self.old_request.processed_by, self.admin)
        self.assertTrue(AdminPortalLog.objects.filter(action="procedure", entity_slug="reject_stale_requests").exists())

    def test_unknown_procedure_shows_error(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("portal_procedure_run", kwargs={"slug": "finish_abandoned_timers"}),
            {"timers-stale_hours": 12},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неизвестная процедура")

    def test_restock_low_stock_consumables_procedure_creates_adjustment(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("portal_procedure_run", kwargs={"slug": "restock_low_stock_consumables"}),
            {"restock-fixed_increase": 4},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_available, 6)
        self.assertTrue(
            InventoryAdjustment.objects.filter(
                equipment=self.equipment,
                reason="Автопополнение по процедуре: +4 шт.",
            ).exists()
        )

    def test_restock_low_stock_consumables_respects_fixed_increase(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("portal_procedure_run", kwargs={"slug": "restock_low_stock_consumables"}),
            {"restock-fixed_increase": 3},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.equipment.refresh_from_db()
        # fixed increase +3 -> available 5 (was 2, delta 3; total 10+3=13)
        self.assertEqual(self.equipment.quantity_available, 5)
        self.assertTrue(
            InventoryAdjustment.objects.filter(
                equipment=self.equipment,
                reason="Автопополнение по процедуре: +3 шт.",
            ).exists()
        )

    def test_simple_restock_and_recover_equipment(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("warehouse_restock"),
            {
                "simple_restock-equipment": self.non_consumable.pk,
                "simple_restock-quantity": 3,
                "simple_restock-non_consumable_action": "increase",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.equipment.refresh_from_db()
        self.non_consumable.refresh_from_db()
        self.assertEqual(self.equipment.quantity_available, 2)
        self.assertEqual(self.non_consumable.status, STATUS_RETIRED)
        self.assertEqual(self.non_consumable.quantity_total, 4)
        self.assertEqual(self.non_consumable.quantity_available, 3)

    def test_warehouse_can_run_simple_restock_procedure(self):
        self.client.force_login(self.warehouse)
        response = self.client.post(
            reverse("warehouse_restock"),
            {
                "simple_restock-equipment": self.equipment.pk,
                "simple_restock-quantity": 1,
                "simple_restock-non_consumable_action": "set_in_stock",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_available, 3)

    def test_sysadmin_can_run_simple_restock_procedure(self):
        self.client.force_login(self.sysadmin)
        response = self.client.post(
            reverse("warehouse_restock"),
            {
                "simple_restock-equipment": self.equipment.pk,
                "simple_restock-quantity": 1,
                "simple_restock-non_consumable_action": "set_in_stock",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_available, 3)

    def test_builder_cannot_access_warehouse_restock(self):
        self.client.force_login(self.builder)
        response = self.client.get(reverse("warehouse_restock"))
        self.assertEqual(response.status_code, 403)

    def test_warehouse_cannot_run_admin_only_procedure(self):
        self.client.force_login(self.warehouse)
        response = self.client.post(
            reverse("portal_procedure_run", kwargs={"slug": "reject_stale_requests"}),
            {"reject-stale_days": 14},
        )
        self.assertEqual(response.status_code, 403)


class RoleEnforcementWebTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.admin = User.objects.create_user(username="site_admin", password=self.password)
        self.warehouse = User.objects.create_user(username="site_warehouse", password=self.password)
        self.sysadmin = User.objects.create_user(username="site_sysadmin", password=self.password)
        self.builder = User.objects.create_user(username="site_builder", password=self.password)
        self.builder_other = User.objects.create_user(username="site_builder_other", password=self.password)

        admin_group, _ = Group.objects.get_or_create(name="Administrator")
        warehouse_group, _ = Group.objects.get_or_create(name="Warehouse")
        sysadmin_group, _ = Group.objects.get_or_create(name="Sysadmin")
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.admin.groups.add(admin_group)
        self.warehouse.groups.add(warehouse_group)
        self.sysadmin.groups.add(sysadmin_group)
        self.builder.groups.add(builder_group)
        self.builder_other.groups.add(builder_group)

        self.workplace = Workplace.objects.create(name="Web roles lab")
        self.category = EquipmentCategory.objects.create(name="Hand tools")
        self.equipment = Equipment.objects.create(
            name="Drill",
            inventory_number="WEB-001",
            category=self.category,
            workplace=self.workplace,
            quantity_total=5,
            quantity_available=5,
        )
        self.checkout = EquipmentCheckout.objects.create(
            equipment=self.equipment,
            quantity=1,
            taken_by=self.builder_other,
            workplace=self.workplace,
            taken_at=timezone.now(),
        )

    def test_checkout_return_requires_post(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("checkout_return", args=[self.checkout.pk]))

        self.assertEqual(response.status_code, 405)

    def test_builder_cannot_return_other_users_checkout(self):
        self.client.force_login(self.builder)

        response = self.client.post(reverse("checkout_return", args=[self.checkout.pk]), follow=True)

        self.assertEqual(response.status_code, 403)
        self.checkout.refresh_from_db()
        self.assertIsNone(self.checkout.returned_at)

    def test_checkout_return_is_disabled_even_for_warehouse(self):
        self.client.force_login(self.warehouse)

        response = self.client.post(reverse("checkout_return", args=[self.checkout.pk]), follow=True)

        self.assertEqual(response.status_code, 403)
        self.checkout.refresh_from_db()
        self.assertIsNone(self.checkout.returned_at)

    def test_history_page_is_admin_only(self):
        self.client.force_login(self.builder)
        denied = self.client.get(reverse("history"))
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.admin)
        allowed = self.client.get(reverse("history"))
        self.assertEqual(allowed.status_code, 200)

    def test_reports_pages_are_admin_or_warehouse_only(self):
        self.client.force_login(self.builder)
        denied_page = self.client.get(reverse("reports"))
        denied_export = self.client.get(reverse("reports_export", args=["materials"]))
        self.assertEqual(denied_page.status_code, 403)
        self.assertEqual(denied_export.status_code, 403)

        self.client.force_login(self.warehouse)
        allowed_page = self.client.get(reverse("reports"))
        allowed_export = self.client.get(reverse("reports_export", args=["materials"]))
        self.assertEqual(allowed_page.status_code, 200)
        self.assertEqual(allowed_export.status_code, 200)

        self.client.force_login(self.sysadmin)
        sysadmin_page = self.client.get(reverse("reports"))
        sysadmin_export = self.client.get(reverse("reports_export", args=["materials"]))
        self.assertEqual(sysadmin_page.status_code, 200)
        self.assertEqual(sysadmin_export.status_code, 200)
        self.assertEqual(sysadmin_export["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn('materials-report.csv', sysadmin_export["Content-Disposition"])

    def test_analytics_dashboard_for_warehouse_first_line_and_denied_for_builder(self):
        self.client.force_login(self.warehouse)
        self.assertEqual(self.client.get(reverse("analytics")).status_code, 200)

        first_line = User.objects.create_user(username="site_first_line_analytics", password=self.password)
        fl_group, _ = Group.objects.get_or_create(name=GROUP_FIRST_LINE_SUPPORT)
        first_line.groups.add(fl_group)
        self.client.force_login(first_line)
        self.assertEqual(self.client.get(reverse("analytics")).status_code, 200)

        self.client.force_login(self.builder)
        self.assertEqual(self.client.get(reverse("analytics")).status_code, 403)

    def test_login_redirects_builder_to_requests_instead_of_forbidden_page(self):
        response = self.client.post(
            reverse("login"),
            {"username": self.builder.username, "password": self.password},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("request_history"))

    def test_login_redirects_warehouse_to_equipment_list(self):
        response = self.client.post(
            reverse("login"),
            {"username": self.warehouse.username, "password": self.password},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("equipment_list"))

    def test_sysadmin_can_access_backup_tools_but_not_import_backup(self):
        self.client.force_login(self.sysadmin)

        tools_response = self.client.get(reverse("data_tools"))
        json_backup_response = self.client.get(reverse("download_json_backup"))

        self.assertEqual(tools_response.status_code, 200)
        self.assertEqual(json_backup_response.status_code, 200)
        self.assertNotContains(tools_response, 'action="/tools/data/import-json/"')
        self.assertNotContains(tools_response, "Импортировать JSON")
        self.assertNotContains(tools_response, 'action="/tools/data/import-postgresql-dump/"')

        import_response = self.client.post(reverse("import_json_backup"), follow=True)
        self.assertEqual(import_response.status_code, 403)

    def test_quality_report_page_is_available_for_admin_and_sysadmin(self):
        self.client.force_login(self.builder)
        denied = self.client.get(reverse("quality_report"))
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.sysadmin)
        sysadmin_response = self.client.get(reverse("quality_report"))
        self.assertEqual(sysadmin_response.status_code, 200)

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("quality_report"))
        self.assertEqual(admin_response.status_code, 200)

    def test_portal_user_form_exposes_only_business_role_fields(self):
        Group.objects.get_or_create(name="Sysadmin")
        form = PortalUserForm()

        self.assertNotIn("is_staff", form.fields)
        self.assertNotIn("is_superuser", form.fields)
        self.assertNotIn("user_permissions", form.fields)
        names = set(form.fields["groups"].queryset.values_list("name", flat=True))
        self.assertTrue({"Administrator", "Builder", "Sysadmin", "Warehouse", "Администратор"}.issubset(names))


@override_settings(
    DATABASES={
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "mpt_tools",
            "USER": "postgres",
            "PASSWORD": "secret",
            "HOST": "localhost",
            "PORT": "5432",
        }
    }
)
class BackupCommandTests(TestCase):
    def _mock_pg_dump(self, command, **kwargs):
        backup_path = Path(command[-1])
        backup_path.write_bytes(b"dump")
        completed = mock.Mock()
        completed.stdout = ""
        completed.stderr = ""
        return completed

    def test_backup_config_reads_env_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            os.environ,
            {
                "BACKUP_DIR": temp_dir,
                "BACKUP_KEEP_COUNT": "9",
                "PG_DUMP_PATH": r"C:\PostgreSQL\bin\pg_dump.exe",
                "BACKUP_CRON_SCHEDULE": "0 2 * * *",
            },
            clear=False,
        ):
            config = get_postgresql_backup_config()

        self.assertEqual(config.output_dir, Path(temp_dir))
        self.assertEqual(config.keep_count, 9)
        self.assertEqual(config.pg_dump_path, r"C:\PostgreSQL\bin\pg_dump.exe")

    @mock.patch("inventory.backup_utils.subprocess.run")
    def test_create_backup_prunes_old_dump_files(self, run_mock):
        run_mock.side_effect = self._mock_pg_dump
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            for idx in range(3):
                old_file = output_dir / f"old_{idx}.dump"
                old_file.write_bytes(b"old")
                stamp = 1_700_000_000 + idx
                os.utime(old_file, (stamp, stamp))

            config = PostgreSQLBackupConfig(
                db_name="mpt_tools",
                db_user="postgres",
                db_password="secret",
                db_host="localhost",
                db_port="5432",
                output_dir=output_dir,
                keep_count=2,
                pg_dump_path="pg_dump",
            )

            result = create_postgresql_backup(config, label="nightly")

            self.assertTrue(result.backup_path.exists())
            self.assertEqual(len(result.removed_files), 2)
            self.assertEqual(len(list(output_dir.glob("*.dump"))), 2)
            self.assertEqual(result.command[0], "pg_dump")
            self.assertIn("nightly", result.backup_path.name)

    @mock.patch("inventory.backup_utils.subprocess.run")
    def test_management_command_creates_backup_file(self, run_mock):
        run_mock.side_effect = self._mock_pg_dump
        with tempfile.TemporaryDirectory() as temp_dir:
            call_command(
                "create_server_backup",
                "--output-dir",
                temp_dir,
                "--keep",
                "3",
                "--label",
                "server",
                "--pg-dump-path",
                "pg_dump",
            )

            dump_files = list(Path(temp_dir).glob("*.dump"))
            self.assertEqual(len(dump_files), 1)
            self.assertIn("server", dump_files[0].name)


class InventoryApiTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.admin_group, _ = Group.objects.get_or_create(name="Administrator")
        self.warehouse_group, _ = Group.objects.get_or_create(name="Warehouse")
        self.sysadmin_group, _ = Group.objects.get_or_create(name="Sysadmin")
        self.builder_group, _ = Group.objects.get_or_create(name="Builder")

        self.admin = User.objects.create_user(username="admin_api", password=self.password)
        self.admin.groups.add(self.admin_group)
        self.warehouse = User.objects.create_user(username="warehouse_api", password=self.password)
        self.warehouse.groups.add(self.warehouse_group)
        self.sysadmin = User.objects.create_user(username="sysadmin_api", password=self.password)
        self.sysadmin.groups.add(self.sysadmin_group)
        self.builder = User.objects.create_user(username="builder_api", password=self.password)
        self.builder.groups.add(self.builder_group)
        self.builder_other = User.objects.create_user(username="builder_other_api", password=self.password)
        self.builder_other.groups.add(self.builder_group)

        self.workplace = Workplace.objects.create(name="Main workshop")
        self.category = EquipmentCategory.objects.create(name="Laptops")
        self.equipment = Equipment.objects.create(
            name="Dell Latitude",
            inventory_number="INV-001",
            category=self.category,
            workplace=self.workplace,
            quantity_total=8,
            quantity_available=8,
        )
        self.pending_request = EquipmentRequest.objects.create(
            requester=self.builder,
            workplace=self.workplace,
            equipment=self.equipment,
            quantity=1,
            request_kind=REQUEST_KIND_BUILDER,
        )

    def api_client_for(self, user):
        client = APIClient()
        logged_in = client.login(username=user.username, password=self.password)
        self.assertTrue(logged_in)
        return client

    def token_client_for(self, token):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        return client

    def create_approved_request(self, user=None, quantity=1):
        return EquipmentRequest.objects.create(
            requester=user or self.builder,
            workplace=self.workplace,
            equipment=self.equipment,
            quantity=quantity,
            request_kind=REQUEST_KIND_BUILDER,
            status=REQUEST_APPROVED,
        )

    def test_api_requires_authentication(self):
        response = self.client.get("/api/v1/equipment/")
        self.assertIn(response.status_code, {401, 403})

    def test_equipment_api_returns_results_for_authorized_user(self):
        client = self.api_client_for(self.builder)
        response = client.get("/api/v1/equipment/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["inventory_number"], "INV-001")

    def test_schema_endpoint_is_available_for_authorized_user(self):
        client = self.api_client_for(self.admin)
        response = client.get("/api/v1/schema/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("openapi", response.content.decode("utf-8"))
        self.assertIn("token", response.content.decode("utf-8").lower())

    def test_api_docs_page_is_available_for_authorized_user(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("api_docs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Документация API")
        self.assertContains(response, "/api/v1/auth/token/")
        self.assertContains(response, "/api/v1/adjustments/")

    def test_token_endpoint_returns_token_and_allows_access(self):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/token/",
            {"username": self.admin.username, "password": self.password},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        token = response.json()["token"]

        token_client = self.token_client_for(token)
        equipment_response = token_client.get("/api/v1/equipment/")
        self.assertEqual(equipment_response.status_code, 200)

    def test_token_endpoint_rejects_invalid_credentials(self):
        client = APIClient()
        response = client.post(
            "/api/v1/auth/token/",
            {"username": self.admin.username, "password": "wrong-password"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_token_revoke_invalidates_existing_token(self):
        token = Token.objects.create(user=self.admin)
        client = self.token_client_for(token.key)

        revoke_response = client.post("/api/v1/auth/token/revoke/")
        self.assertEqual(revoke_response.status_code, 204)
        self.assertFalse(Token.objects.filter(user=self.admin).exists())

        denied_response = client.get("/api/v1/equipment/")
        self.assertIn(denied_response.status_code, {401, 403})

    def test_api_permission_error_returns_human_readable_payload(self):
        client = self.api_client_for(self.builder)

        response = client.patch(
            f"/api/v1/equipment/{self.equipment.pk}/",
            {"name": "Not allowed"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["code"], "permission_denied")
        self.assertIn("detail", payload)

    def test_admin_can_create_and_delete_equipment_via_api(self):
        client = self.api_client_for(self.admin)
        response = client.post(
            "/api/v1/equipment/",
            {
                "name": "HP ProBook",
                "inventory_number": "INV-002",
                "category": self.category.pk,
                "workplace": self.workplace.pk,
                "quantity_total": 3,
                "quantity_available": 3,
                "is_consumable": False,
                "status": "in_stock",
                "low_stock_threshold": 1,
                "inventory_interval_days": 180,
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 201)
        created_id = response.json()["id"]
        self.assertTrue(Equipment.objects.filter(inventory_number="INV-002").exists())

        delete_response = client.delete(f"/api/v1/equipment/{created_id}/")
        self.assertEqual(delete_response.status_code, 204)
        self.assertIsNotNone(Equipment.all_objects.get(pk=created_id).deleted_at)

    def test_warehouse_can_update_stock_fields_but_not_equipment_name(self):
        client = self.api_client_for(self.warehouse)

        response = client.patch(
            f"/api/v1/equipment/{self.equipment.pk}/",
            {"quantity_available": 6},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_available, 6)

        denied = client.patch(
            f"/api/v1/equipment/{self.equipment.pk}/",
            {"name": "Renamed by warehouse"},
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

    def test_builder_can_create_and_update_own_request_but_cannot_change_status(self):
        client = self.api_client_for(self.builder)

        create_response = client.post(
            "/api/v1/requests/",
            {
                "workplace": self.workplace.pk,
                "equipment": self.equipment.pk,
                "quantity": 1,
                "comment": "Need one laptop",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        request_id = create_response.json()["id"]
        created_request = EquipmentRequest.objects.get(pk=request_id)
        self.assertEqual(created_request.requester, self.builder)
        self.assertEqual(created_request.request_kind, REQUEST_KIND_BUILDER)

        update_response = client.patch(
            f"/api/v1/requests/{request_id}/",
            {"comment": "Need one laptop urgently"},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)

        denied_response = client.patch(
            f"/api/v1/requests/{request_id}/",
            {"status": "approved"},
            format="json",
        )
        self.assertEqual(denied_response.status_code, 403)

    def test_warehouse_can_process_request_status(self):
        client = self.api_client_for(self.warehouse)

        response = client.patch(
            f"/api/v1/requests/{self.pending_request.pk}/",
            {"status": "approved"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.pending_request.refresh_from_db()
        self.assertEqual(self.pending_request.status, "approved")
        self.assertEqual(self.pending_request.processed_by, self.warehouse)
        self.assertIsNotNone(self.pending_request.processed_at)

    def test_timers_api_is_not_exposed(self):
        client = self.api_client_for(self.builder)

        response = client.get("/api/v1/timers/")

        self.assertEqual(response.status_code, 404)

    def test_material_usage_depleting_stock_sets_equipment_retired(self):
        eq = Equipment.objects.create(
            name="Cable bundle",
            inventory_number="INV-CAB-RET",
            category=self.category,
            workplace=self.workplace,
            quantity_total=2,
            quantity_available=2,
            is_consumable=True,
            status=STATUS_IN_STOCK,
        )
        MaterialUsage.objects.create(
            equipment=eq,
            workplace=self.workplace,
            quantity=2,
            used_by=self.builder,
            note="deplete",
        )
        eq.refresh_from_db()
        self.assertEqual(eq.quantity_total, 0)
        self.assertEqual(eq.quantity_available, 0)
        self.assertEqual(eq.status, STATUS_RETIRED)

    def test_material_usage_partial_does_not_change_status_to_retired(self):
        eq = Equipment.objects.create(
            name="Cable partial",
            inventory_number="INV-CAB-PAR",
            category=self.category,
            workplace=self.workplace,
            quantity_total=5,
            quantity_available=5,
            is_consumable=True,
            status=STATUS_IN_STOCK,
        )
        MaterialUsage.objects.create(
            equipment=eq,
            workplace=self.workplace,
            quantity=2,
            used_by=self.builder,
            note="partial",
        )
        eq.refresh_from_db()
        self.assertEqual(eq.status, STATUS_IN_STOCK)

    def test_builder_can_create_usage_with_audit_actor(self):
        client = self.api_client_for(self.builder)

        response = client.post(
            "/api/v1/usage/",
            {
                "equipment": self.equipment.pk,
                "workplace": self.workplace.pk,
                "quantity": 1,
                "note": "Used in field work",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        usage = MaterialUsage.objects.get(pk=response.json()["id"])
        self.assertEqual(usage.used_by, self.builder)

        audit_entry = AuditLog.objects.filter(
            content_type__model="materialusage",
            object_id=str(usage.pk),
            action="created",
        ).latest("created_at")
        self.assertEqual(audit_entry.actor, self.builder)

    def test_builder_api_lists_are_scoped_to_own_operational_records(self):
        MaterialUsage.objects.create(
            equipment=self.equipment,
            workplace=self.workplace,
            quantity=1,
            used_by=self.builder,
            note="My usage",
        )
        MaterialUsage.objects.create(
            equipment=self.equipment,
            workplace=self.workplace,
            quantity=1,
            used_by=self.builder_other,
            note="Other usage",
        )
        own_request = EquipmentRequest.objects.create(
            requester=self.builder,
            workplace=self.workplace,
            equipment=self.equipment,
            quantity=1,
            request_kind=REQUEST_KIND_BUILDER,
        )
        EquipmentRequest.objects.create(
            requester=self.builder_other,
            workplace=self.workplace,
            equipment=self.equipment,
            quantity=1,
            request_kind=REQUEST_KIND_BUILDER,
        )
        own_checkout = EquipmentCheckout.objects.create(
            equipment=self.equipment,
            quantity=1,
            taken_by=self.builder,
            workplace=self.workplace,
            taken_at=timezone.now(),
        )
        EquipmentCheckout.objects.create(
            equipment=self.equipment,
            quantity=1,
            taken_by=self.builder_other,
            workplace=self.workplace,
            taken_at=timezone.now(),
        )

        client = self.api_client_for(self.builder)

        requests_response = client.get("/api/v1/requests/")
        usage_response = client.get("/api/v1/usage/")
        checkouts_response = client.get("/api/v1/checkouts/")

        self.assertEqual([item["id"] for item in requests_response.json()], [own_request.pk, self.pending_request.pk])
        self.assertEqual([item["used_by"] for item in usage_response.json()], [self.builder.pk])
        self.assertEqual([item["id"] for item in checkouts_response.json()], [own_checkout.pk])

    def test_warehouse_api_lists_can_see_all_operational_records(self):
        MaterialUsage.objects.create(
            equipment=self.equipment,
            workplace=self.workplace,
            quantity=1,
            used_by=self.builder,
            note="Builder usage",
        )
        MaterialUsage.objects.create(
            equipment=self.equipment,
            workplace=self.workplace,
            quantity=1,
            used_by=self.builder_other,
            note="Other usage",
        )

        client = self.api_client_for(self.warehouse)
        usage_response = client.get("/api/v1/usage/")

        self.assertEqual(usage_response.status_code, 200)
        self.assertEqual(len(usage_response.json()), 2)

    def test_warehouse_can_create_and_update_adjustment_with_stock_sync(self):
        client = self.api_client_for(self.warehouse)

        create_response = client.post(
            "/api/v1/adjustments/",
            {
                "equipment": self.equipment.pk,
                "delta": 2,
                "reason": "Restocked",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        adjustment_id = create_response.json()["id"]
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_total, 10)
        self.assertEqual(self.equipment.quantity_available, 10)

        update_response = client.patch(
            f"/api/v1/adjustments/{adjustment_id}/",
            {"delta": 3},
            format="json",
        )

        self.assertEqual(update_response.status_code, 200)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_total, 11)
        self.assertEqual(self.equipment.quantity_available, 11)

    def test_admin_can_delete_adjustment_and_restore_stock(self):
        adjustment = InventoryAdjustment.objects.create(
            equipment=self.equipment,
            delta=2,
            reason="Temporary stock correction",
            created_by=self.warehouse,
        )
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_total, 10)

        client = self.api_client_for(self.admin)
        response = client.delete(f"/api/v1/adjustments/{adjustment.pk}/")

        self.assertEqual(response.status_code, 204)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_total, 8)
        self.assertEqual(self.equipment.quantity_available, 8)
        self.assertIsNotNone(InventoryAdjustment.all_objects.get(pk=adjustment.pk).deleted_at)

    def test_builder_can_create_checkout_and_mark_it_returned(self):
        approved_request = self.create_approved_request(user=self.builder, quantity=1)
        client = self.api_client_for(self.builder)

        create_response = client.post(
            "/api/v1/checkouts/",
            {
                "equipment": self.equipment.pk,
                "workplace": self.workplace.pk,
                "related_request": approved_request.pk,
                "quantity": 1,
                "note": "Taking for work",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        checkout_id = create_response.json()["id"]
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_available, 7)

        return_response = client.patch(
            f"/api/v1/checkouts/{checkout_id}/",
            {"returned_at": timezone.now().isoformat()},
            format="json",
        )

        self.assertEqual(return_response.status_code, 200)
        checkout = EquipmentCheckout.objects.get(pk=checkout_id)
        self.assertIsNotNone(checkout.returned_at)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_available, 8)


class EquipmentSplitRepairWebTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.warehouse_user = User.objects.create_user(username="split_wh", password=self.password)
        warehouse_group, _ = Group.objects.get_or_create(name="Warehouse")
        self.warehouse_user.groups.add(warehouse_group)
        self.builder = User.objects.create_user(username="split_builder", password=self.password)
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.builder.groups.add(builder_group)
        self.cat = EquipmentCategory.objects.create(name="Power tools")
        self.wp = Workplace.objects.create(name="Shop")
        self.drills = Equipment.objects.create(
            name="Дрели сетевые",
            inventory_number="DRILL-BULK-001",
            serial_number="DRILL-BULK-001",
            category=self.cat,
            workplace=self.wp,
            is_consumable=False,
            status=STATUS_IN_STOCK,
            quantity_total=4,
            quantity_available=4,
        )

    def test_split_repair_creates_row_and_reduces_original(self):
        self.client.force_login(self.warehouse_user)
        url = reverse("equipment_split_repair", kwargs={"equipment_id": self.drills.pk})
        response = self.client.post(url, {"qty": "1", "unit_serial": "DRILL-SN-7788"})
        self.assertEqual(response.status_code, 302)
        self.drills.refresh_from_db()
        self.assertEqual(self.drills.quantity_total, 3)
        self.assertEqual(self.drills.quantity_available, 3)
        repair = Equipment.objects.get(inventory_number="DRILL-SN-7788")
        self.assertEqual(repair.quantity_total, 1)
        self.assertEqual(repair.status, STATUS_REPAIR)

    def test_builder_cannot_split_repair(self):
        self.client.force_login(self.builder)
        url = reverse("equipment_split_repair", kwargs={"equipment_id": self.drills.pk})
        response = self.client.post(url, {"qty": "1", "unit_serial": "X-1"})
        self.assertEqual(response.status_code, 403)


class RequestEquipmentConditionSplitTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.processor = User.objects.create_user(username="req_proc", password=self.password)
        support_group, _ = Group.objects.get_or_create(name=GROUP_FIRST_LINE_SUPPORT)
        self.processor.groups.add(support_group)
        self.requester = User.objects.create_user(username="req_owner", password=self.password)
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.requester.groups.add(builder_group)
        self.cat = EquipmentCategory.objects.create(name="Networking")
        self.wp = Workplace.objects.create(name="Office")
        self.router = Equipment.objects.create(
            name="Роутер",
            inventory_number="RTR-BULK-01",
            serial_number="RTR-BULK-01",
            category=self.cat,
            workplace=self.wp,
            is_consumable=False,
            status=STATUS_IN_STOCK,
            quantity_total=5,
            quantity_available=5,
        )
        self.req = EquipmentRequest.objects.create(
            requester=self.requester,
            workplace=self.wp,
            equipment=self.router,
            quantity=2,
            request_kind=REQUEST_KIND_BUILDER,
            status=REQUEST_PENDING,
        )

    def test_request_partial_repair_creates_new_row(self):
        self.client.force_login(self.processor)
        response = self.client.post(
            reverse("request_update_equipment_condition", kwargs={"request_id": self.req.pk}),
            {
                "equipment_condition": "repair",
                "equipment_condition_qty": "2",
                "equipment_condition_unit_serial": "RTR-REP-02",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.router.refresh_from_db()
        self.assertEqual(self.router.quantity_total, 3)
        self.assertEqual(self.router.quantity_available, 3)
        split_row = Equipment.objects.get(inventory_number="RTR-REP-02")
        self.assertEqual(split_row.status, STATUS_REPAIR)
        self.assertEqual(split_row.quantity_total, 2)
        self.assertEqual(split_row.quantity_available, 2)

    def test_request_partial_retired_creates_new_row_unavailable(self):
        self.client.force_login(self.processor)
        response = self.client.post(
            reverse("request_update_equipment_condition", kwargs={"request_id": self.req.pk}),
            {
                "equipment_condition": "retired",
                "equipment_condition_qty": "1",
                "equipment_condition_unit_serial": "RTR-RET-01",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.router.refresh_from_db()
        self.assertEqual(self.router.quantity_total, 4)
        self.assertEqual(self.router.quantity_available, 4)
        retired_row = Equipment.objects.get(inventory_number="RTR-RET-01")
        self.assertEqual(retired_row.status, STATUS_RETIRED)
        self.assertEqual(retired_row.quantity_total, 1)
        self.assertEqual(retired_row.quantity_available, 0)


class NotificationBellTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.requester = User.objects.create_user(username="bell_requester", password=self.password)
        self.processor = User.objects.create_user(username="bell_processor", password=self.password)
        self.workplace = Workplace.objects.create(name="Bell workshop")
        self.category = EquipmentCategory.objects.create(name="Bell cat")
        self.equipment = Equipment.objects.create(
            name="Bell tool",
            inventory_number="INV-BELL-01",
            category=self.category,
            workplace=self.workplace,
            quantity_total=5,
            quantity_available=5,
            is_consumable=True,
            status=STATUS_IN_STOCK,
        )

    def test_request_thread_unread_for_requester_cleared_after_opening_detail(self):
        req = EquipmentRequest.objects.create(
            requester=self.requester,
            workplace=self.workplace,
            equipment=self.equipment,
            quantity=1,
            request_kind=REQUEST_KIND_WRITEOFF,
            status=REQUEST_PENDING,
            processed_by=self.processor,
        )
        EquipmentRequestMessage.objects.create(request=req, author=self.processor, body="Здравствуйте")
        self.assertEqual(unread_request_message_count(self.requester), 1)
        self.assertEqual(unread_request_message_count(self.processor), 0)

        self.client.force_login(self.requester)
        response = self.client.get(reverse("request_detail", kwargs={"request_id": req.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(unread_request_message_count(self.requester), 0)

    def test_notifications_page_requires_login(self):
        response = self.client.get(reverse("notifications"))
        self.assertEqual(response.status_code, 302)

    def test_notifications_page_renders_for_authenticated_user(self):
        self.client.force_login(self.requester)
        response = self.client.get(reverse("notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Уведомления")
