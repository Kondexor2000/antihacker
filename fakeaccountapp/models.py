from django.db import models
from django.conf import settings
from django.utils import timezone


class LoginLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    ip_address = models.GenericIPAddressField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"], name="login_user_created_idx"),
        ]

    def __str__(self):
        return f"{self.user} from {self.ip_address} at {self.created_at}"


class BackupEvent(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    ip_address = models.GenericIPAddressField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="backup_status_created_idx"),
        ]

    def __str__(self):
        return f"Backup {self.pk} for {self.user} ({self.status})"


class UsernameIdentity(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="username_identity",
    )
    normalized_username = models.CharField(max_length=450, unique=True)

    def __str__(self):
        return self.normalized_username
