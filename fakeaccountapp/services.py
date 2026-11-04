import logging
from datetime import timedelta
from ipaddress import ip_address
from threading import Event, Thread

from django.conf import settings
from django.db import DatabaseError, close_old_connections, transaction
from django.db.models import F, Q
from django.utils import timezone

from .backup import create_secure_backup
from .models import BackupEvent, LoginLog


security_logger = logging.getLogger("fakeaccount.security")


def _is_trusted_proxy(address):
    return any(
        address.version == network.version and address in network
        for network in settings.TRUSTED_PROXY_IPS
    )


def get_client_ip(request):
    remote_addr = request.META.get("REMOTE_ADDR", "").strip()
    try:
        remote_ip = ip_address(remote_addr)
    except ValueError:
        return None

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if not forwarded_for or not _is_trusted_proxy(remote_ip):
        return str(remote_ip)

    try:
        forwarded_ips = [ip_address(value.strip()) for value in forwarded_for.split(",")]
    except ValueError:
        security_logger.warning(
            "Ignoring invalid X-Forwarded-For from trusted proxy %s",
            remote_ip,
        )
        return str(remote_ip)

    for address in reversed([*forwarded_ips, remote_ip]):
        if not _is_trusted_proxy(address):
            return str(address)

    return str(forwarded_ips[0]) if forwarded_ips else str(remote_ip)


def process_login(user, request):
    ip_address = get_client_ip(request)
    if not ip_address:
        security_logger.warning(
            "Login received without a client IP for user_id=%s",
            user.pk,
        )
        return None

    with transaction.atomic():
        previous_login = (
            LoginLog.objects.filter(user=user)
            .order_by("-created_at", "-pk")
            .first()
        )
        LoginLog.objects.create(user=user, ip_address=ip_address)

        if previous_login is None or previous_login.ip_address == ip_address:
            return None

        event = BackupEvent.objects.create(user=user, ip_address=ip_address)

    security_logger.warning(
        "backup.queued",
        extra={"event_id": event.pk, "user_id": user.pk},
    )
    return event


def run_backup_event(event):
    stop_heartbeat = Event()
    heartbeat = Thread(
        target=_refresh_backup_lease,
        args=(event.pk, stop_heartbeat),
        daemon=True,
    )
    heartbeat.start()
    try:
        create_secure_backup(skip_duplicate=True)
    except Exception as exc:
        stop_heartbeat.set()
        heartbeat.join(timeout=5)
        event.status = (
            BackupEvent.Status.FAILED
            if event.attempts >= settings.BACKUP_MAX_ATTEMPTS
            else BackupEvent.Status.PENDING
        )
        event.error_message = f"{type(exc).__name__}: {exc}"[:4000]
        if event.status == BackupEvent.Status.PENDING:
            retry_delay = min(3600, 30 * (2 ** (event.attempts - 1)))
            event.next_attempt_at = timezone.now() + timedelta(seconds=retry_delay)
        event.save(
            update_fields=[
                "status",
                "error_message",
                "next_attempt_at",
                "updated_at",
            ]
        )
        security_logger.exception(
            "backup.failed",
            extra={"event_id": event.pk, "attempt": event.attempts},
        )
        return False

    stop_heartbeat.set()
    heartbeat.join(timeout=5)
    event.status = BackupEvent.Status.SUCCEEDED
    event.save(update_fields=["status", "error_message", "updated_at"])
    security_logger.info(
        "backup.succeeded",
        extra={"event_id": event.pk, "attempt": event.attempts},
    )
    return True


def _refresh_backup_lease(event_id, stop_event):
    interval = max(0.1, settings.BACKUP_PROCESSING_LEASE_SECONDS / 3)
    close_old_connections()
    try:
        while not stop_event.wait(interval):
            updated = BackupEvent.objects.filter(
                pk=event_id,
                status=BackupEvent.Status.PROCESSING,
            ).update(updated_at=timezone.now())
            if not updated:
                security_logger.error(
                    "Backup event lease was lost for event_id=%s",
                    event_id,
                )
                return
    except DatabaseError:
        security_logger.exception(
            "Could not refresh the processing lease for backup event_id=%s",
            event_id,
        )
    finally:
        close_old_connections()


def _claim_next_backup_event(include_failed=True, excluded_ids=()):
    stale_before = timezone.now() - timedelta(
        seconds=settings.BACKUP_PROCESSING_LEASE_SECONDS
    )
    pending_statuses = [BackupEvent.Status.PENDING]
    if include_failed:
        pending_statuses.append(BackupEvent.Status.FAILED)
    candidates = BackupEvent.objects.filter(
        Q(status__in=pending_statuses)
        & (Q(next_attempt_at__lte=timezone.now()) | Q(next_attempt_at__isnull=True))
        | Q(status=BackupEvent.Status.PROCESSING, updated_at__lt=stale_before)
    ).exclude(pk__in=excluded_ids).order_by("created_at", "pk")

    for candidate in candidates[:100]:
        previous_status = candidate.status
        claim_filter = BackupEvent.objects.filter(
            pk=candidate.pk,
            status=previous_status,
            updated_at=candidate.updated_at,
        )
        if previous_status == BackupEvent.Status.PROCESSING:
            claim_filter = claim_filter.filter(updated_at__lt=stale_before)

        claimed_at = timezone.now()
        claimed = claim_filter.update(
            status=BackupEvent.Status.PROCESSING,
            attempts=F("attempts") + 1,
            error_message="",
            updated_at=claimed_at,
        )
        if claimed:
            candidate.refresh_from_db()
            return candidate

    return None


def process_backup_events(limit=None, include_failed=True):
    succeeded = 0
    failed = 0
    processed = 0
    processed_ids = set()

    while limit is None or processed < limit:
        event = _claim_next_backup_event(
            include_failed=include_failed,
            excluded_ids=processed_ids,
        )
        if event is None:
            break
        processed += 1
        processed_ids.add(event.pk)
        if run_backup_event(event):
            succeeded += 1
        else:
            failed += 1

    return processed, succeeded, failed


def retry_backup_events():
    _, succeeded, failed = process_backup_events()
    return succeeded, failed
