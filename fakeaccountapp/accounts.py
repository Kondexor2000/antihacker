from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from .models import LoginLog, UsernameIdentity
from .validators import normalize_username


User = get_user_model()


class UsernameIdentityConflict(Exception):
    pass


def register_user(form, ip_address=None):
    user = form.save(commit=False)
    user.set_password(form.cleaned_data["password"])
    normalized_username = normalize_username(user.get_username())
    if not normalized_username:
        raise UsernameIdentityConflict

    try:
        with transaction.atomic():
            user.save()
            UsernameIdentity.objects.create(
                user=user,
                normalized_username=normalized_username,
            )
            if ip_address:
                LoginLog.objects.create(user=user, ip_address=ip_address)
    except IntegrityError as exc:
        if (
            User.objects.filter(username=user.get_username()).exists()
            or UsernameIdentity.objects.filter(
                normalized_username=normalized_username
            ).exists()
        ):
            raise UsernameIdentityConflict from exc
        raise

    return user
