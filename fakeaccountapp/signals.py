import logging

from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from .services import process_login


security_logger = logging.getLogger("fakeaccount.security")


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    security_logger.info("login.succeeded", extra={"user_id": user.pk})
    try:
        process_login(user, request)
    except Exception:
        security_logger.exception(
            "login.side_effects_failed",
            extra={"user_id": user.pk, "outcome": "queue_error"},
        )


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request, **kwargs):
    security_logger.warning(
        "login.failed",
        extra={"outcome": "failure"},
    )
