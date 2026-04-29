from django.apps import AppConfig
import os
import sys
import threading


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"

    def ready(self):
        if not self._should_auto_refresh_quality_report():
            return
        self._start_quality_report_refresh()

    @staticmethod
    def _should_auto_refresh_quality_report() -> bool:
        # Run only when the web server starts (avoid recursion during test commands).
        if len(sys.argv) < 2 or sys.argv[1] != "runserver":
            return False
        enabled = os.environ.get("AUTO_REFRESH_QUALITY_REPORT_ON_START", "false").strip().lower()
        if enabled not in {"1", "true", "yes", "on"}:
            return False
        # With Django autoreload, execute only in the main serving process.
        if os.environ.get("RUN_MAIN") == "true":
            return True
        return "--noreload" in sys.argv

    @staticmethod
    def _start_quality_report_refresh():
        def _worker():
            from .quality_report import generate_quality_report

            print("[quality] Auto-refreshing JSON report on startup...")
            try:
                report = generate_quality_report()
                print(
                    "[quality] Report updated. "
                    f"Status={report['summary']['overall_status']}, "
                    f"commands={report['summary']['total_commands']}"
                )
            except Exception as exc:
                print(f"[quality] Auto-refresh failed: {exc}")

        threading.Thread(target=_worker, name="quality-report-refresh", daemon=True).start()

