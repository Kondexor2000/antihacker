from django.core.management.base import BaseCommand, CommandError

from fakeaccountapp.services import retry_backup_events


class Command(BaseCommand):
    help = "Retry pending or failed backups created after logins from new IPs."

    def handle(self, *args, **options):
        succeeded, failed = retry_backup_events()
        self.stdout.write(
            self.style.SUCCESS(f"Backups completed: {succeeded}; still failing: {failed}.")
        )
        if failed:
            raise CommandError(
                f"{failed} backup event(s) failed again; inspect them in Django admin."
            )
