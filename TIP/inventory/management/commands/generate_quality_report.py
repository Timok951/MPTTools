from django.core.management.base import BaseCommand

from inventory.quality_report import generate_quality_report


class Command(BaseCommand):
    help = "Run validation commands and save the latest site quality report."

    def handle(self, *args, **options):
        report = generate_quality_report()
        summary = report["summary"]
        self.stdout.write(
            self.style.SUCCESS(
                f"Quality report generated: {summary['passed_commands']}/{summary['total_commands']} commands passed, "
                f"{summary['total_tests_ran']} tests run."
            )
        )
