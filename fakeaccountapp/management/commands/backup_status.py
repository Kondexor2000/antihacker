import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections
from django.db.models import Count, Q
from django.utils import timezone

from fakeaccountapp.models import BackupEvent


class Command(BaseCommand):
    help = "Print database and backup-queue health as a JSON snapshot."

    def handle(self, *args, **options):
        try:
            connections["default"].ensure_connection()
        except DatabaseError as exc:
            raise CommandError("The configured database is unavailable.") from exc

        counts = dict(
            BackupEvent.objects.values("status")
            .annotate(total=Count("pk"))
            .values_list("status", "total")
        )
        now = timezone.now()
        due_work = (
            Q(
                status=BackupEvent.Status.PENDING,
                next_attempt_at__lte=now,
            )
            | Q(
                status=BackupEvent.Status.PENDING,
                next_attempt_at__isnull=True,
            )
            | Q(
                status=BackupEvent.Status.PROCESSING,
                updated_at__lt=now
                - timedelta(seconds=settings.BACKUP_PROCESSING_LEASE_SECONDS),
            )
        )
        oldest_due_event = (
            BackupEvent.objects.filter(due_work)
            .order_by("created_at", "pk")
            .values_list("created_at", flat=True)
            .first()
        )
        oldest_due_age = None
        if oldest_due_event is not None:
            oldest_due_age = round(
                max(0, (now - oldest_due_event).total_seconds()),
                2,
            )

        report = {
            "as_of": now.isoformat(),
            "database": "ok",
            "queue": {
                status: counts.get(status, 0)
                for status in BackupEvent.Status.values
            },
            "oldest_due_age_seconds": oldest_due_age,
        }
        self.stdout.write(json.dumps(report, sort_keys=True))
