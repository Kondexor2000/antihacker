from django.contrib import admin

from .models import BackupEvent, LoginLog, ProjectMetadata


@admin.register(ProjectMetadata)
class ProjectMetadataAdmin(admin.ModelAdmin):
    list_display = ("project_name", "may_support_military")
    list_filter = ("may_support_military",)
    search_fields = ("project_name", "military_support_rationale")


@admin.register(LoginLog)
class LoginLogAdmin(admin.ModelAdmin):
    list_display = ("user", "ip_address", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__username", "ip_address")


@admin.register(BackupEvent)
class BackupEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "status",
        "attempts",
        "next_attempt_at",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("user__username", "ip_address", "error_message")
    readonly_fields = (
        "user",
        "ip_address",
        "status",
        "attempts",
        "error_message",
        "next_attempt_at",
        "created_at",
        "updated_at",
    )
