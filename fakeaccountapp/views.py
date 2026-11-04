import logging

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.db import DatabaseError, connections
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .accounts import UsernameIdentityConflict, register_user
from .backup import decrypt_secure_backup
from .forms import RegistrationForm
from .services import get_client_ip


security_logger = logging.getLogger("fakeaccount.security")


def livez(request):
    return HttpResponse("ok", content_type="text/plain")


def healthz(request):
    try:
        connections["default"].ensure_connection()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def register(request):
    if request.method == "POST":
        form = RegistrationForm(request.POST)

        if form.is_valid():
            ip = get_client_ip(request)
            try:
                register_user(form, ip_address=ip)
            except UsernameIdentityConflict:
                form.add_error(
                    "username",
                    "Ta nazwa użytkownika jest zbyt podobna do istniejącego konta.",
                )
            else:
                return redirect("login")
    else:
        form = RegistrationForm()

    return render(request, "register.html", {"form": form})


@staff_member_required
def download_decrypted_backup(request):
    try:
        backup_data = decrypt_secure_backup()
    except (FileNotFoundError, ValueError, OSError):
        security_logger.exception("Unable to verify or decrypt the requested backup.")
        return HttpResponse("Backup unavailable.", status=503)
    response = HttpResponse(backup_data, content_type="application/json")
    response["Content-Disposition"] = 'attachment; filename="backup.json"'
    return response


@login_required
def account_home(request):
    backup_event = (
        request.user.backupevent_set
        .order_by("-created_at", "-pk")
        .first()
    )
    return render(request, "account.html", {"backup_event": backup_event})


@require_POST
def logout_view(request):
    logout(request)
    return redirect(settings.LOGOUT_REDIRECT_URL)
