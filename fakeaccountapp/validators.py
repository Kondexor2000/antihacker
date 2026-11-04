import unicodedata

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import UsernameIdentity

User = get_user_model()


def normalize_username(username: str) -> str:
    """
    Zamienia znaki często używane do podszywania się pod inne konta
    oraz usuwa znaki specjalne.
    """

    username = unicodedata.normalize("NFKC", username).casefold().strip()

    replacements = {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }

    for old, new in replacements.items():
        username = username.replace(old, new)

    # usunięcie wszystkiego poza literami i cyframi
    username = "".join(character for character in username if character.isalnum())

    return username


def validate_username_uniqueness(username: str):
    """
    Blokuje konta wyglądające identycznie po normalizacji.
    """

    normalized_new = normalize_username(username)
    if not normalized_new:
        raise ValidationError("Nazwa użytkownika musi zawierać literę lub cyfrę.")

    if UsernameIdentity.objects.filter(normalized_username=normalized_new).exists():
        raise ValidationError("Ta nazwa użytkownika jest zbyt podobna do istniejącego konta.")

    claimed_user_ids = UsernameIdentity.objects.values("user_id")
    existing_users = User.objects.exclude(pk__in=claimed_user_ids).values_list(
        "username",
        flat=True,
    )

    for existing_username in existing_users:
        normalized_existing = normalize_username(existing_username)

        if normalized_new == normalized_existing:
            raise ValidationError(
                f'Nazwa użytkownika jest zbyt podobna do istniejącego konta "{existing_username}".'
            )