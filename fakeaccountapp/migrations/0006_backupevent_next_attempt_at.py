import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fakeaccountapp", "0005_usernameidentity"),
    ]

    operations = [
        migrations.AddField(
            model_name="backupevent",
            name="next_attempt_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                null=True,
            ),
        ),
    ]
