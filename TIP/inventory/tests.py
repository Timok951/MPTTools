from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from time import perf_counter
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from assets.models import Equipment, EquipmentCheckout, InventoryAdjustment
from audit.models import AuditLog
from audit.models import AdminPortalLog
from core.models import EquipmentCategory, UserPreference, Workplace
from operations.models import (
    REQUEST_APPROVED,
    REQUEST_KIND_BUILDER,
    EquipmentRequest,
    MaterialUsage,
    WorkTimer,
)


class TimerAndPreferenceViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="builder", password="secret123")
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.user.groups.add(builder_group)
        self.workplace = Workplace.objects.create(name="Lab 101")

    def test_quick_timer_start_creates_active_timer(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("timer_quick_start"),
            {
                "workplace": self.workplace.pk,
                "equipment": "",
                "note": "Quick test timer",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorkTimer.objects.count(), 1)
        timer = WorkTimer.objects.get()
        self.assertEqual(timer.user, self.user)
        self.assertEqual(timer.workplace, self.workplace)
        self.assertEqual(timer.note, "Quick test timer")
        self.assertIsNone(timer.ended_at)

    def test_preferences_view_persists_user_settings(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("user_preferences"),
            {
                "theme_variant": "contrast",
                "preferred_language": "ru",
                "page_size": 50,
                "date_display_format": "iso",
                "default_timer_status": "active",
                "default_request_status": "pending",
                "default_request_kind": "builder",
                "default_usage_period_days": 14,
                "default_checkout_status": "returned",
                "hotkeys_enabled": "on",
                "show_hotkey_legend": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        pref = UserPreference.objects.get(user=self.user)
        self.assertEqual(pref.theme_variant, "contrast")
        self.assertEqual(pref.preferred_language, "ru")
        self.assertEqual(pref.page_size, 50)
        self.assertEqual(pref.date_display_format, "iso")
        self.assertEqual(pref.default_timer_status, "active")
        self.assertEqual(pref.default_request_status, "pending")
        self.assertEqual(pref.default_request_kind, "builder")
        self.assertEqual(pref.default_usage_period_days, 14)
        self.assertEqual(pref.default_checkout_status, "returned")
        self.assertTrue(pref.hotkeys_enabled)
        self.assertTrue(pref.show_hotkey_legend)

    def test_preferences_page_renders_dark_theme_option(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("user_preferences"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="dark"')
        self.assertContains(response, "Язык интерфейса")


class LightweightPerformanceTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.user = User.objects.create_user(username="perf_builder", password=self.password)
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.user.groups.add(builder_group)
        self.workplace = Workplace.objects.create(name="Perf Lab")

    def test_timer_panel_opens_quickly_enough_for_small_fixture(self):
        self.client.force_login(self.user)

        started = perf_counter()
        response = self.client.get(reverse("timer_panel"))
        elapsed = perf_counter() - started

        self.assertEqual(response.status_code, 200)
        self.assertLess(elapsed, 1.5)

    def test_quick_timer_create_flow_stays_lightweight(self):
        self.client.force_login(self.user)

        started = perf_counter()
        response = self.client.post(
            reverse("timer_quick_start"),
            {
                "workplace": self.workplace.pk,
                "equipment": "",
                "note": "Perf smoke test",
            },
            follow=True,
        )
        elapsed = perf_counter() - started

        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorkTimer.objects.filter(user=self.user, note="Perf smoke test").count(), 1)
        self.assertLess(elapsed, 1.5)


class AdminProcedureTests(TestCase):
    def setUp(self):
        self.password = "secret123"
        self.admin = User.objects.create_user(username="portal_admin", password=self.password)
        admin_group, _ = Group.objects.get_or_create(name="Administrator")
        self.admin.groups.add(admin_group)

        self.builder = User.objects.create_user(username="portal_builder", password=self.password)
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        self.builder.groups.add(builder_group)

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
        self.old_request = EquipmentRequest.objects.create(
            requester=self.builder,
            workplace=self.workplace,
            equipment=self.equipment,
            quantity=1,
            request_kind=REQUEST_KIND_BUILDER,
            requested_at=timezone.now() - timedelta(days=20),
        )
        self.old_timer = WorkTimer.objects.create(
            user=self.builder,
            workplace=self.workplace,
            equipment=self.equipment,
            started_at=timezone.now() - timedelta(hours=30),
            note="Forgot to stop",
        )

    def test_admin_portal_shows_procedures(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("portal_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Business procedures")
        self.assertContains(response, "Run procedure")

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

    def test_finish_abandoned_timers_procedure_closes_old_timers(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("portal_procedure_run", kwargs={"slug": "finish_abandoned_timers"}),
            {"timers-stale_hours": 12},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.old_timer.refresh_from_db()
        self.assertIsNotNone(self.old_timer.ended_at)

    def test_restock_low_stock_consumables_procedure_creates_adjustment(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("portal_procedure_run", kwargs={"slug": "restock_low_stock_consumables"}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.quantity_available, 5)
        self.assertTrue(
            InventoryAdjustment.objects.filter(
                equipment=self.equipment,
                reason="Automatic restock to low-stock threshold by admin procedure.",
            ).exists()
        )


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
        self.other_timer = WorkTimer.objects.create(
            user=self.builder_other,
            workplace=self.workplace,
            equipment=self.equipment,
            note="Other builder timer",
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

    def test_builder_cannot_update_other_users_timer(self):
        client = self.api_client_for(self.builder)

        response = client.patch(
            f"/api/v1/timers/{self.other_timer.pk}/",
            {"note": "Trying to edit another timer"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

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
