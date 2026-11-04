import unicodedata

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def normalize_username(username):
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
    normalized = unicodedata.normalize("NFKC", username).casefold().strip()
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return "".join(character for character in normalized if character.isalnum())


def create_existing_username_identities(apps, schema_editor):
    user_app, user_model = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(user_app, user_model)
    UsernameIdentity = apps.get_model("fakeaccountapp", "UsernameIdentity")
    identities = {}

    for user in User.objects.using(schema_editor.connection.alias).iterator():
        username_field = getattr(User, "USERNAME_FIELD", "username")
        normalized = normalize_username(getattr(user, username_field))
        if not normalized:
            raise RuntimeError(
                f"User {user.pk} has a username that normalizes to an empty value."
            )
        if normalized in identities:
            raise RuntimeError(
                "Existing usernames normalize to the same identity; resolve the "
                f"collision between user IDs {identities[normalized]} and {user.pk} "
                "before applying this migration."
            )
        identities[normalized] = user.pk
        UsernameIdentity.objects.using(schema_editor.connection.alias).create(
            user_id=user.pk,
            normalized_username=normalized,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("fakeaccountapp", "0004_backupevent"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="loginlog",
            index=models.Index(
                fields=["user", "created_at"],
                name="login_user_created_idx",
            ),
        ),
        migrations.CreateModel(
            name="UsernameIdentity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("normalized_username", models.CharField(max_length=450, unique=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="username_identity",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.RunPython(
            create_existing_username_identities,
            migrations.RunPython.noop,
        ),
    ]
