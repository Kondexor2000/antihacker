import math
import time

from django.core.management.base import BaseCommand, CommandError

from fakeaccountapp.services import process_backup_events


class Command(BaseCommand):
    help = "Process queued login backups in a separate worker process."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process the current queue once and exit.",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=5,
            help="Seconds to wait between queue checks (default: 5).",
        )

    def handle(self, *args, **options):
        interval = options["interval"]
        if not math.isfinite(interval) or interval <= 0:
            raise CommandError("--interval must be a finite number greater than zero.")

        self.stdout.write("Backup worker started.")
        try:
            while True:
                processed, succeeded, failed = process_backup_events(
                    include_failed=False
                )
                if processed:
                    self.stdout.write(
                        f"Backups processed: {succeeded} succeeded; {failed} failed."
                    )
                if options["once"]:
                    if failed:
                        raise CommandError(f"{failed} queued backup(s) failed.")
                    return
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("\nBackup worker stopped.")
