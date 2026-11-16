import importlib.util
import ipaddress
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.contrib.staticfiles.views import serve as serve_static
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .backup import create_secure_backup, decrypt_secure_backup, get_backup_paths
from .forms import RegistrationForm
from .middleware import TrustedProxyMiddleware
from .models import BackupEvent, LoginLog, ProjectMetadata, UsernameIdentity
from .network import get_private_ip_addresses
from .services import get_client_ip, process_backup_events

from fakeaccount.settings import _get_secret_key


User = get_user_model()


class ConfigurationTests(TestCase):
    def test_production_secret_must_be_explicit_and_strong(self):
        for secret in ("", "replace-with-a-secret", "too-short"):
            with self.subTest(secret_length=len(secret)):
                with self.assertRaises(ImproperlyConfigured):
                    _get_secret_key(secret, debug=False)

        self.assertEqual(_get_secret_key("x" * 50, debug=False), "x" * 50)

    def test_development_can_generate_an_ephemeral_secret(self):
        secret = _get_secret_key("replace-with-a-secret", debug=True)

        self.assertGreaterEqual(len(secret), 50)
        self.assertNotEqual(secret, _get_secret_key("replace-with-a-secret", debug=True))

    @override_settings(DEBUG=True)
    def test_cookie_security_defaults_are_explicit(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.SESSION_COOKIE_SECURE, not settings.DEBUG)
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.CSRF_COOKIE_SECURE, not settings.DEBUG)


class ProjectMetadataTests(TestCase):
    def test_military_support_potential_is_recorded(self):
        metadata = ProjectMetadata.objects.get(project_name="FakeAccount")
        rationale = metadata.military_support_rationale.casefold()

        self.assertTrue(metadata.may_support_military)
        self.assertIn("encrypted backups", rationale)
        self.assertIn("different from its previously recorded ip", rationale)
        self.assertIn("decrypted", rationale)


class RegistrationTests(TestCase):
    def test_register_creates_user_with_hashed_password(self):
        response = self.client.post(
            reverse("register"),
            {"username": "jan", "password": "StrongPass123"},
            REMOTE_ADDR="127.0.0.1",
        )

        self.assertRedirects(response, reverse("login"))
        user = User.objects.get(username="jan")
        self.assertTrue(user.check_password("StrongPass123"))
        self.assertTrue(LoginLog.objects.filter(user=user, ip_address="127.0.0.1").exists())

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://127.0.0.1:8000"])
    def test_register_accepts_https_origin_with_csrf_checks(self):
        client = self.client_class(enforce_csrf_checks=True)
        get_response = client.get(reverse("register"), secure=True, SERVER_NAME="127.0.0.1", SERVER_PORT=8000)
        csrf_token = get_response.cookies["csrftoken"].value

        response = client.post(
            reverse("register"),
            {"username": "https-user", "password": "StrongPass123", "csrfmiddlewaretoken": csrf_token},
            secure=True,
            SERVER_NAME="127.0.0.1",
            SERVER_PORT=8000,
            HTTP_ORIGIN="https://127.0.0.1:8000",
        )

        self.assertRedirects(response, reverse("login"))
        self.assertTrue(User.objects.filter(username="https-user").exists())

    def test_registration_post_requires_csrf_token(self):
        client = self.client_class(enforce_csrf_checks=True)

        response = client.post(
            reverse("register"),
            {"username": "csrf-user", "password": "StrongPass123"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username="csrf-user").exists())

    def test_registration_rejects_similar_username(self):
        User.objects.create_user(username="admin", password="pass")

        form = RegistrationForm({"username": "adm1n", "password": "StrongPass123"})

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_registration_rejects_username_that_normalizes_to_empty(self):
        form = RegistrationForm({"username": "...", "password": "StrongPass123"})

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_registration_rejects_compatibility_unicode_username_collision(self):
        User.objects.create_user(username="alice", password="StrongPass123")
        form = RegistrationForm(
            {"username": "Ａlice", "password": "AnotherPass456"}
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_username_identity_constraint_rejects_duplicate_normalized_names(self):
        first = User.objects.create_user(username="alice", password="StrongPass123")
        second = User.objects.create_user(username="alice2", password="StrongPass123")
        UsernameIdentity.objects.create(user=first, normalized_username="alice")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UsernameIdentity.objects.create(
                    user=second,
                    normalized_username="alice",
                )

        self.assertFalse(UsernameIdentity.objects.filter(user=second).exists())

    def test_login_redirects_regular_user_to_account_page(self):
        User.objects.create_user(username="account-user", password="StrongPass123")

        response = self.client.post(
            reverse("login"),
            {"username": "account-user", "password": "StrongPass123"},
        )

        self.assertRedirects(response, reverse("account_home"))
        self.assertContains(self.client.get(reverse("account_home")), "account-user")

    def test_account_page_shows_backup_job_status(self):
        user = User.objects.create_user(username="backup-status-user", password="StrongPass123")
        BackupEvent.objects.create(user=user, ip_address="192.0.2.1")
        self.client.force_login(user)

        response = self.client.get(reverse("account_home"))

        self.assertContains(response, "oczekuje w kolejce")

    def test_account_page_does_not_expose_backup_failure_details(self):
        user = User.objects.create_user(username="backup-failed-user", password="StrongPass123")
        BackupEvent.objects.create(
            user=user,
            ip_address="192.0.2.12",
            status=BackupEvent.Status.FAILED,
            error_message="private storage path",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("account_home"))

        self.assertContains(response, "Nie udało się utworzyć kopii")
        self.assertNotContains(response, "private storage path")


class RequestCorrelationTests(TestCase):
    def test_response_returns_a_validated_request_id(self):
        request_id = "f4f78f0e-d5b5-48a7-b983-83623fb4c582"

        response = self.client.get(
            reverse("healthz"),
            HTTP_X_REQUEST_ID=request_id,
        )

        self.assertEqual(response["X-Request-ID"], request_id)

    def test_invalid_request_id_is_replaced(self):
        response = self.client.get(reverse("healthz"), HTTP_X_REQUEST_ID="not-a-uuid")

        self.assertNotEqual(response["X-Request-ID"], "not-a-uuid")
        self.assertEqual(len(response["X-Request-ID"]), 36)

    def test_backup_status_command_reports_database_and_queue_counts(self):
        user = User.objects.create_user(username="queue-metrics-user", password="StrongPass123")
        BackupEvent.objects.create(user=user, ip_address="192.0.2.11")
        from io import StringIO

        output = StringIO()
        call_command("backup_status", stdout=output)
        report = json.loads(output.getvalue())

        self.assertEqual(report["database"], "ok")
        self.assertEqual(report["queue"]["pending"], 1)
        self.assertEqual(report["queue"]["failed"], 0)
        self.assertIsNotNone(report["oldest_due_age_seconds"])


class ReactStaticAssetTests(TestCase):
    def test_react_javascript_build_is_served_as_a_javascript_module(self):
        request = RequestFactory().get("/static/react/assets/app.js")
        response = serve_static(
            request,
            path="react/assets/app.js",
            insecure=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")


class LoginIpSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="anna", password="StrongPass123")

    def test_untrusted_forwarded_for_is_ignored(self):
        request = type(
            "Request",
            (),
            {"META": {"HTTP_X_FORWARDED_FOR": "10.0.0.2, 10.0.0.3", "REMOTE_ADDR": "127.0.0.1"}},
        )()

        self.assertEqual(get_client_ip(request), "127.0.0.1")

    @override_settings(TRUSTED_PROXY_IPS=(ipaddress.ip_network("10.0.0.0/8"),))
    def test_trusted_proxy_chain_uses_rightmost_untrusted_address(self):
        request = type(
            "Request",
            (),
            {
                "META": {
                    "HTTP_X_FORWARDED_FOR": "203.0.113.4, 198.51.100.7, 10.0.0.3",
                    "REMOTE_ADDR": "10.0.0.2",
                }
            },
        )()

        self.assertEqual(get_client_ip(request), "198.51.100.7")

    @override_settings(TRUSTED_PROXY_IPS=(ipaddress.ip_network("10.0.0.0/8"),))
    def test_malformed_forwarded_for_falls_back_to_proxy_address(self):
        request = type(
            "Request",
            (),
            {
                "META": {
                    "HTTP_X_FORWARDED_FOR": "not-an-ip",
                    "REMOTE_ADDR": "10.0.0.2",
                }
            },
        )()

        self.assertEqual(get_client_ip(request), "10.0.0.2")

    def test_invalid_remote_address_is_not_recorded(self):
        request = type(
            "Request",
            (),
            {"META": {"HTTP_X_FORWARDED_FOR": "10.0.0.2", "REMOTE_ADDR": "invalid"}},
        )()

        self.assertIsNone(get_client_ip(request))

    def test_untrusted_peer_cannot_spoof_forwarded_proto(self):
        request = RequestFactory().get(
            "/",
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="203.0.113.10",
        )
        response = TrustedProxyMiddleware(
            lambda req: HttpResponse(str(req.is_secure()))
        )(request)

        self.assertEqual(response.content, b"False")

    @override_settings(TRUSTED_PROXY_IPS=(ipaddress.ip_network("10.0.0.0/8"),))
    def test_trusted_peer_can_assert_forwarded_https(self):
        request = RequestFactory().get(
            "/",
            HTTP_X_FORWARDED_PROTO="https",
            REMOTE_ADDR="10.0.0.2",
        )
        response = TrustedProxyMiddleware(
            lambda req: HttpResponse(str(req.is_secure()))
        )(request)

        self.assertEqual(response.content, b"True")

    @patch("fakeaccountapp.services.create_secure_backup")
    def test_login_from_different_ip_after_registration_creates_backup(self, create_backup):
        self.client.post(
            reverse("register"),
            {"username": "marek", "password": "StrongPass123"},
            REMOTE_ADDR="127.0.0.1",
        )
        with self.assertLogs("fakeaccount.security", level="WARNING") as captured:
            self.client.post(
                reverse("login"),
                {"username": "marek", "password": "StrongPass123"},
                REMOTE_ADDR="10.0.0.5",
            )

        user = User.objects.get(username="marek")
        self.assertEqual(LoginLog.objects.filter(user=user).count(), 2)
        create_backup.assert_not_called()
        self.assertIn("backup.queued", captured.output[0])
        event = BackupEvent.objects.get(user=user)
        self.assertEqual(event.status, BackupEvent.Status.PENDING)
        self.assertEqual(event.attempts, 0)

    @patch("fakeaccountapp.services.create_secure_backup")
    def test_login_from_same_ip_after_registration_does_not_queue_backup(self, create_backup):
        self.client.post(
            reverse("register"),
            {"username": "ewa", "password": "StrongPass123"},
            REMOTE_ADDR="127.0.0.1",
        )
        self.client.post(
            reverse("login"),
            {"username": "ewa", "password": "StrongPass123"},
            REMOTE_ADDR="127.0.0.1",
        )

        create_backup.assert_not_called()
        self.assertFalse(BackupEvent.objects.exists())

    @patch("fakeaccountapp.services.create_secure_backup")
    def test_worker_command_processes_pending_backup_event(self, create_backup):
        self.client.post(
            reverse("register"),
            {"username": "worker-user", "password": "StrongPass123"},
            REMOTE_ADDR="127.0.0.1",
        )
        self.client.post(
            reverse("login"),
            {"username": "worker-user", "password": "StrongPass123"},
            REMOTE_ADDR="10.0.0.7",
        )
        event = BackupEvent.objects.get(user__username="worker-user")
        self.assertEqual(event.status, BackupEvent.Status.PENDING)

        call_command("process_backups", "--once")

        event.refresh_from_db()
        self.assertEqual(event.status, BackupEvent.Status.SUCCEEDED)
        self.assertEqual(event.attempts, 1)
        create_backup.assert_called_once_with(skip_duplicate=True)

    @patch("fakeaccountapp.services.create_secure_backup")
    def test_worker_claims_queue_rows_without_retry_time(self, create_backup):
        event = BackupEvent.objects.create(
            user=self.user,
            ip_address="192.0.2.13",
            next_attempt_at=None,
        )

        self.assertEqual(
            process_backup_events(include_failed=False),
            (1, 1, 0),
        )
        event.refresh_from_db()
        self.assertEqual(event.status, BackupEvent.Status.SUCCEEDED)
        create_backup.assert_called_once_with(skip_duplicate=True)

    @override_settings(BACKUP_MAX_ATTEMPTS=1)
    @patch(
        "fakeaccountapp.services.create_secure_backup",
        side_effect=[RuntimeError("disk unavailable"), None],
    )
    def test_failed_backup_does_not_block_login_and_can_be_retried(self, create_backup):
        self.client.post(
            reverse("register"),
            {"username": "retry-user", "password": "StrongPass123"},
            REMOTE_ADDR="127.0.0.1",
        )

        response = self.client.post(
            reverse("login"),
            {"username": "retry-user", "password": "StrongPass123"},
            REMOTE_ADDR="10.0.0.9",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/account/")
        event = BackupEvent.objects.get(user__username="retry-user")
        self.assertEqual(event.status, BackupEvent.Status.PENDING)
        self.assertEqual(create_backup.call_count, 0)

        with self.assertLogs("fakeaccount.security", level="ERROR"):
            self.assertEqual(process_backup_events(), (1, 0, 1))
        event.refresh_from_db()
        self.assertEqual(event.status, BackupEvent.Status.FAILED)
        self.assertEqual(event.attempts, 1)
        self.assertEqual(event.error_message, "RuntimeError: disk unavailable")

        call_command("retry_backups")

        event.refresh_from_db()
        self.assertEqual(event.status, BackupEvent.Status.SUCCEEDED)
        self.assertEqual(event.attempts, 2)
        self.assertEqual(create_backup.call_count, 2)

    @patch(
        "fakeaccountapp.services.create_secure_backup",
        side_effect=RuntimeError("temporary storage outage"),
    )
    def test_transient_failure_is_delayed_before_automatic_retry(self, create_backup):
        event = BackupEvent.objects.create(
            user=self.user,
            ip_address="192.0.2.9",
        )

        with self.assertLogs("fakeaccount.security", level="ERROR"):
            self.assertEqual(process_backup_events(include_failed=False), (1, 0, 1))

        event.refresh_from_db()
        self.assertEqual(event.status, BackupEvent.Status.PENDING)
        self.assertEqual(event.attempts, 1)
        self.assertGreater(event.next_attempt_at, timezone.now())
        self.assertEqual(process_backup_events(include_failed=False), (0, 0, 0))
        create_backup.assert_called_once_with(skip_duplicate=True)


class LogoutTests(TestCase):
    def test_logout_get_is_not_allowed(self):
        User.objects.create_user(username="logout-user", password="StrongPass123")
        self.client.post(
            reverse("login"),
            {"username": "logout-user", "password": "StrongPass123"},
        )

        response = self.client.get(reverse("logout"))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_post_logs_out(self):
        user = User.objects.create_user(
            username="logout-post-user",
            password="StrongPass123",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, "/login/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_csrf_token(self):
        user = User.objects.create_user(
            username="logout-csrf-user",
            password="StrongPass123",
        )
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(user)

        self.assertEqual(client.post(reverse("logout")).status_code, 403)

        csrf_response = client.get(reverse("register"))
        csrf_token = csrf_response.cookies["csrftoken"].value
        response = client.post(
            reverse("logout"),
            {"csrfmiddlewaretoken": csrf_token},
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", client.session)


class LoginProtectionTests(TestCase):
    def test_axes_blocks_login_after_failure_limit(self):
        User.objects.create_user(username="protected-user", password="StrongPass123")

        for attempt in range(5):
            response = self.client.post(
                reverse("login"),
                {"username": "protected-user", "password": "wrong-password"},
                REMOTE_ADDR="10.0.0.8",
            )
            self.assertEqual(response.status_code, 200 if attempt < 4 else 429)

        response = self.client.post(
            reverse("login"),
            {"username": "protected-user", "password": "StrongPass123"},
            REMOTE_ADDR="10.0.0.8",
        )

        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)


class NetworkConfigTests(TestCase):
    @patch("fakeaccountapp.network.socket.getaddrinfo")
    @patch("fakeaccountapp.network.socket.socket")
    def test_private_ip_detection_ignores_loopback_and_link_local(self, socket_class, getaddrinfo):
        getaddrinfo.return_value = [
            (None, None, None, None, ("127.0.0.1", 0)),
            (None, None, None, None, ("169.254.1.1", 0)),
            (None, None, None, None, ("192.168.1.22", 0)),
        ]
        socket_class.side_effect = OSError

        self.assertEqual(get_private_ip_addresses(), ["192.168.1.22"])


class AdminBackupDownloadTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="staff",
            password="StrongPass123",
            is_staff=True,
        )

    @patch("fakeaccountapp.views.decrypt_secure_backup", return_value=b'[{"model": "auth.user"}]')
    def test_staff_can_download_decrypted_backup(self, decrypt_backup):
        self.client.post(
            reverse("login"),
            {"username": "staff", "password": "StrongPass123"},
        )

        response = self.client.get(reverse("download_decrypted_backup"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("backup.json", response["Content-Disposition"])
        self.assertEqual(response.content, b'[{"model": "auth.user"}]')
        decrypt_backup.assert_called_once_with()

    def test_anonymous_user_is_redirected_from_backup_download(self):
        response = self.client.get(reverse("download_decrypted_backup"))

        self.assertEqual(response.status_code, 302)

    def test_non_staff_user_cannot_download_backup(self):
        user = User.objects.create_user(username="regular-user", password="StrongPass123")
        self.client.force_login(user)

        response = self.client.get(reverse("download_decrypted_backup"))

        self.assertEqual(response.status_code, 302)

    @patch(
        "fakeaccountapp.views.decrypt_secure_backup",
        side_effect=ValueError("Backup authentication failed."),
    )
    def test_staff_is_not_sent_a_backup_when_integrity_check_fails(self, decrypt_backup):
        self.client.force_login(self.staff)

        with self.assertLogs("fakeaccount.security", level="ERROR"):
            response = self.client.get(reverse("download_decrypted_backup"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"Backup unavailable.")
        self.assertNotEqual(response["Content-Type"], "application/json")
        decrypt_backup.assert_called_once_with()


class SecureBackupIntegrationTests(TestCase):
    def setUp(self):
        if importlib.util.find_spec("cryptography") is None:
            self.skipTest("cryptography is not installed")
        self.backup_dir = Path(tempfile.mkdtemp(prefix="fakeaccount-test-backups-"))
        settings_override = override_settings(SECURE_BACKUP_DIR=self.backup_dir)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.addCleanup(shutil.rmtree, self.backup_dir)

    def test_backup_encrypts_decrypts_and_skips_duplicate_dump(self):
        User.objects.create_user(username="backup-user", password="StrongPass123")

        first = create_secure_backup(skip_duplicate=True)
        second = create_secure_backup(skip_duplicate=True)
        decrypted = decrypt_secure_backup()

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertIn(b"backup-user", decrypted)

    def test_backup_fixture_restores_into_a_clean_database(self):
        user = User.objects.create_user(username="restore-user", password="StrongPass123")
        BackupEvent.objects.create(user=user, ip_address="192.0.2.10")
        result = create_secure_backup(skip_duplicate=False)
        fixture_path = self.backup_dir / "restore-fixture.json"
        fixture_path.write_bytes(decrypt_secure_backup(result["backup_path"]))
        restore_database_path = self.backup_dir / "restore.sqlite3"
        restore_code = """
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fakeaccount.settings")
import django
django.setup()
from django.conf import settings
settings.DATABASES["default"]["NAME"] = os.environ["RESTORE_DATABASE_PATH"]
from django.contrib.auth import get_user_model
from django.core.management import call_command
from fakeaccountapp.models import BackupEvent
call_command("migrate", verbosity=0)
call_command("loaddata", os.environ["RESTORE_FIXTURE_PATH"], verbosity=0)
user = get_user_model().objects.get(username="restore-user")
assert BackupEvent.objects.filter(user=user, status=BackupEvent.Status.PENDING).exists()
"""
        environment = os.environ.copy()
        environment["RESTORE_DATABASE_PATH"] = str(restore_database_path)
        environment["RESTORE_FIXTURE_PATH"] = str(fixture_path)
        subprocess.run(
            [sys.executable, "-c", restore_code],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_tampered_backup_is_rejected(self):
        result = create_secure_backup()
        path = Path(result["backup_path"])
        encrypted = bytearray(path.read_bytes())
        encrypted[-1] ^= 0x01
        path.write_bytes(encrypted)

        with self.assertRaisesRegex(ValueError, "authentication"):
            decrypt_secure_backup()

    def test_missing_backup_raises_instead_of_returning_empty_data(self):
        with self.assertRaises(FileNotFoundError):
            decrypt_secure_backup()

    @override_settings(BACKUP_RETENTION_COUNT=2)
    @patch(
        "fakeaccountapp.backup._dump_database",
        side_effect=[b"snapshot-one", b"snapshot-two", b"snapshot-three"],
    )
    def test_backup_retention_keeps_only_the_newest_files(self, dump_database):
        create_secure_backup(skip_duplicate=False)
        create_secure_backup(skip_duplicate=False)
        newest = create_secure_backup(skip_duplicate=False)

        backup_path, _ = get_backup_paths()
        retained = list(backup_path.parent.glob("backup_*.enc"))
        self.assertEqual(len(retained), 2)
        self.assertEqual(decrypt_secure_backup(), b"snapshot-three")
        self.assertTrue(Path(newest["backup_path"]).exists())
        self.assertEqual(dump_database.call_count, 3)

    @patch("fakeaccountapp.backup._dump_database", return_value=b"shared-snapshot")
    def test_concurrent_backup_creation_is_serialized(self, dump_database):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: create_secure_backup(), range(4)))

        self.assertEqual(sum(result["created"] for result in results), 1)
        backup_path, _ = get_backup_paths()
        self.assertEqual(len(list(backup_path.parent.glob("backup_*.enc"))), 1)
        self.assertEqual(decrypt_secure_backup(), b"shared-snapshot")
        self.assertEqual(dump_database.call_count, 4)

    def test_key_rotation_keeps_old_backups_decryptable_while_key_is_retained(self):
        keyring = {"old": os.urandom(32), "new": os.urandom(32)}
        with override_settings(
            BACKUP_ENCRYPTION_KEYS=keyring,
            BACKUP_ACTIVE_KEY_ID="old",
        ):
            previous = create_secure_backup(skip_duplicate=False)

        with override_settings(
            BACKUP_ENCRYPTION_KEYS=keyring,
            BACKUP_ACTIVE_KEY_ID="new",
        ):
            current = create_secure_backup(skip_duplicate=False)
            self.assertEqual(decrypt_secure_backup(), decrypt_secure_backup(previous["backup_path"]))
            self.assertTrue(Path(current["backup_path"]).exists())

        with override_settings(
            BACKUP_ENCRYPTION_KEYS={"new": keyring["new"]},
            BACKUP_ACTIVE_KEY_ID="new",
        ):
            with self.assertRaisesRegex(ValueError, "not configured"):
                decrypt_secure_backup(previous["backup_path"])

    def test_legacy_backup_can_be_authenticated_and_recovered(self):
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes, hmac, padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from django.conf import settings

        legacy_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"fakeaccount-secure-backup-v1",
            info=b"database-backup",
        ).derive(settings.BACKUP_LEGACY_SECRET_KEY.encode("utf-8"))
        data = b"legacy-snapshot"
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        iv = os.urandom(16)
        encryptor = Cipher(
            algorithms.AES(legacy_key),
            modes.CBC(iv),
            backend=default_backend(),
        ).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()
        mac = hmac.HMAC(legacy_key, hashes.SHA256())
        mac.update(iv + ciphertext)
        backup_path, meta_path = get_backup_paths()
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(iv + ciphertext)
        meta_path.write_text(
            json.dumps(
                {
                    "algorithm": "AES-256-CBC-HMAC-SHA256",
                    "dump_sha256": hashlib.sha256(data).hexdigest(),
                    "hmac": mac.finalize().hex(),
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(decrypt_secure_backup(), data)

        from io import StringIO

        output = StringIO()
        call_command(
            "secure_backup",
            "--decrypt",
            "--input",
            str(backup_path),
            stdout=output,
        )
        self.assertEqual(output.getvalue().strip(), data.decode("utf-8"))
