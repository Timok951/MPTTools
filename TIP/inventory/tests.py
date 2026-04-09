from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from assets.models import Equipment
from core.models import EquipmentCategory, UserPreference, Workplace
from operations.models import WorkTimer


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


class InventoryApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="apiuser", password="secret123")
        admin_group, _ = Group.objects.get_or_create(name="Administrator")
        self.user.groups.add(admin_group)
        self.workplace = Workplace.objects.create(name="Main workshop")
        self.category = EquipmentCategory.objects.create(name="Laptops")
        self.equipment = Equipment.objects.create(
            name="Dell Latitude",
            inventory_number="INV-001",
            category=self.category,
            workplace=self.workplace,
            quantity_total=5,
            quantity_available=4,
        )

    def test_api_requires_authentication(self):
        response = self.client.get("/api/v1/equipment/")
        self.assertEqual(response.status_code, 403)

    def test_equipment_api_returns_results_for_authorized_user(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/v1/equipment/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["inventory_number"], "INV-001")

    def test_schema_endpoint_is_available_for_authorized_user(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/v1/schema/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("openapi", response.content.decode("utf-8"))

    def test_api_docs_page_is_available_for_authorized_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("api_docs"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Документация API")

    def test_admin_can_create_equipment_via_api(self):
        self.client.force_login(self.user)
        response = self.client.post(
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
        self.assertTrue(Equipment.objects.filter(inventory_number="INV-002").exists())

    def test_non_admin_cannot_create_equipment_via_api(self):
        builder = User.objects.create_user(username="builder2", password="secret123")
        builder_group, _ = Group.objects.get_or_create(name="Builder")
        builder.groups.add(builder_group)
        self.client.force_login(builder)

        response = self.client.post(
            "/api/v1/equipment/",
            {
                "name": "Blocked item",
                "inventory_number": "INV-003",
                "quantity_total": 1,
                "quantity_available": 1,
                "is_consumable": False,
                "status": "in_stock",
                "low_stock_threshold": 0,
                "inventory_interval_days": 180,
            },
        )

        self.assertEqual(response.status_code, 403)
