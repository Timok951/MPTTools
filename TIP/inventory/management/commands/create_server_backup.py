from django.core.management.base import BaseCommand

from inventory.backup_utils import create_postgresql_backup, get_postgresql_backup_config


class Command(BaseCommand):
    help = "Create a PostgreSQL backup and prune old dump files."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", dest="output_dir", help="Directory where .dump files will be stored.")
        parser.add_argument("--keep", dest="keep_count", type=int, help="How many latest backups to keep.")
        parser.add_argument("--label", dest="label", default="", help="Optional label to append to the dump filename.")
        parser.add_argument("--pg-dump-path", dest="pg_dump_path", help="Path to pg_dump executable.")

    def handle(self, *args, **options):
        config = get_postgresql_backup_config(
            output_dir=options.get("output_dir"),
            keep_count=options.get("keep_count"),
            pg_dump_path=options.get("pg_dump_path"),
        )
        result = create_postgresql_backup(config, label=options.get("label", ""))

        self.stdout.write(self.style.SUCCESS(f"Backup created: {result.backup_path}"))
        if result.removed_files:
            self.stdout.write("Removed old backups:")
            for path in result.removed_files:
                self.stdout.write(f" - {path}")
        else:
            self.stdout.write("No old backups were removed.")
