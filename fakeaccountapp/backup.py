import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import portalocker
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone as django_timezone


BACKUP_FILE = "backup.enc"
META_FILE = "backup_meta.json"
_MAGIC = b"FAKEACCOUNT-BACKUP\x00\x02"
_HEADER_LENGTH_SIZE = 4
_NONCE_LENGTH = 12
_MAX_HEADER_LENGTH = 16_384
_BACKUP_NAME = re.compile(r"backup_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{32}\.enc$")


def get_backup_dir():
    return Path(settings.SECURE_BACKUP_DIR)


def get_backup_paths():
    backup_dir = get_backup_dir()
    return backup_dir / BACKUP_FILE, backup_dir / META_FILE


def _derive_key(secret, info):
    return HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"fakeaccount-backup-key-v2",
        info=info,
    ).derive(secret.encode("utf-8"))


def _get_keyring():
    if settings.BACKUP_ENCRYPTION_KEYS:
        return settings.BACKUP_ENCRYPTION_KEYS
    return {
        settings.BACKUP_ACTIVE_KEY_ID: _derive_key(
            settings.SECRET_KEY,
            b"development-backup-encryption",
        )
    }


def _dump_database():
    from io import StringIO

    output = StringIO()
    call_command(
        "dumpdata",
        natural_foreign=True,
        natural_primary=True,
        stdout=output,
    )
    return output.getvalue().encode("utf-8")


def _backup_files():
    backup_dir = get_backup_dir()
    files = [
        path
        for path in backup_dir.glob("backup_*.enc")
        if _BACKUP_NAME.fullmatch(path.name)
    ]
    return sorted(files, key=lambda path: path.name, reverse=True)


def _encode_backup(data, digest):
    key_id = settings.BACKUP_ACTIVE_KEY_ID
    header = {
        "algorithm": "AES-256-GCM",
        "created_at": django_timezone.now().isoformat(timespec="seconds"),
        "dump_sha256": digest,
        "key_id": key_id,
        "version": 2,
    }
    header_bytes = json.dumps(
        header,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    aad = _MAGIC + len(header_bytes).to_bytes(_HEADER_LENGTH_SIZE, "big") + header_bytes
    nonce = os.urandom(_NONCE_LENGTH)
    encrypted = AESGCM(_get_keyring()[key_id]).encrypt(nonce, data, aad)
    return aad + nonce + encrypted


def _decode_v2(encrypted):
    prefix_length = len(_MAGIC)
    if not encrypted.startswith(_MAGIC):
        raise ValueError("Unsupported backup format.")
    if len(encrypted) < prefix_length + _HEADER_LENGTH_SIZE:
        raise ValueError("Backup file is truncated.")

    header_length = int.from_bytes(
        encrypted[prefix_length : prefix_length + _HEADER_LENGTH_SIZE],
        "big",
    )
    if not 0 < header_length <= _MAX_HEADER_LENGTH:
        raise ValueError("Backup header has an invalid length.")
    header_start = prefix_length + _HEADER_LENGTH_SIZE
    header_end = header_start + header_length
    payload_start = header_end + _NONCE_LENGTH
    if payload_start + 16 > len(encrypted):
        raise ValueError("Backup file is truncated.")

    try:
        header = json.loads(encrypted[header_start:header_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Backup header is invalid.") from exc
    if (
        not isinstance(header, dict)
        or header.get("version") != 2
        or header.get("algorithm") != "AES-256-GCM"
        or not isinstance(header.get("key_id"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", str(header.get("dump_sha256", "")))
    ):
        raise ValueError("Backup header is invalid.")

    key = _get_keyring().get(header["key_id"])
    if key is None:
        raise ValueError(
            f"Backup encryption key {header['key_id']!r} is not configured."
        )
    aad = encrypted[:header_end]
    nonce = encrypted[header_end:payload_start]
    try:
        data = AESGCM(key).decrypt(nonce, encrypted[payload_start:], aad)
    except InvalidTag as exc:
        raise ValueError("Backup authentication failed.") from exc

    if not hashlib.sha256(data).hexdigest() == header["dump_sha256"]:
        raise ValueError("Backup content digest does not match its authenticated header.")
    return data, header


def _decode_legacy(backup_path, meta_path):
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, hmac, padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF as LegacyHKDF

    if not backup_path.exists() or not meta_path.exists():
        raise FileNotFoundError("No encrypted backup was found.")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Legacy backup metadata is invalid.") from exc
    if (
        not isinstance(meta, dict)
        or meta.get("algorithm") != "AES-256-CBC-HMAC-SHA256"
        or not isinstance(meta.get("hmac"), str)
    ):
        raise ValueError("Legacy backup metadata is invalid.")

    secret = settings.BACKUP_LEGACY_SECRET_KEY.encode("utf-8")
    key = LegacyHKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"fakeaccount-secure-backup-v1",
        info=b"database-backup",
    ).derive(secret)
    encrypted = backup_path.read_bytes()
    if len(encrypted) < 32 or (len(encrypted) - 16) % 16:
        raise ValueError("Legacy backup file is truncated.")

    iv, ciphertext = encrypted[:16], encrypted[16:]
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(iv + ciphertext)
    try:
        mac.verify(bytes.fromhex(meta["hmac"]))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Legacy backup authentication failed.") from exc

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        data = unpadder.update(padded_data) + unpadder.finalize()
    except ValueError as exc:
        raise ValueError("Legacy backup padding is invalid.") from exc
    if meta.get("dump_sha256") and hashlib.sha256(data).hexdigest() != meta["dump_sha256"]:
        raise ValueError("Legacy backup content digest does not match.")
    return data


def _read_backup(path):
    encrypted = path.read_bytes()
    return _decode_v2(encrypted)


def _prune_backups():
    for path in _backup_files()[settings.BACKUP_RETENTION_COUNT :]:
        path.unlink()


def create_secure_backup(skip_duplicate=True):
    backup_dir = get_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(backup_dir, 0o700)
    lock_path = backup_dir / ".backup.lock"

    with portalocker.Lock(
        str(lock_path),
        mode="a",
        timeout=settings.BACKUP_LOCK_TIMEOUT,
    ):
        data = _dump_database()
        digest = hashlib.sha256(data).hexdigest()
        existing_backups = _backup_files()
        if existing_backups:
            _, previous_header = _read_backup(existing_backups[0])
            if skip_duplicate and previous_header["dump_sha256"] == digest:
                _prune_backups()
                return {
                    "created": False,
                    "reason": "duplicate",
                    "backup_path": str(existing_backups[0]),
                }

        payload = _encode_backup(data, digest)
        created_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"backup_{created_at}_{uuid.uuid4().hex}.enc"
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".backup-",
            suffix=".tmp",
            dir=backup_dir,
        )
        try:
            with os.fdopen(file_descriptor, "wb") as backup_file:
                backup_file.write(payload)
                backup_file.flush()
                os.fsync(backup_file.fileno())
            os.replace(temporary_name, backup_path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

        _prune_backups()

    return {
        "created": True,
        "backup_path": str(backup_path),
    }


def decrypt_secure_backup(backup_path=None):
    if backup_path is not None:
        path = Path(backup_path)
        if path.name == BACKUP_FILE:
            return _decode_legacy(path, path.with_name(META_FILE))
        return _read_backup(path)[0]

    backups = _backup_files()
    if backups:
        return _read_backup(backups[0])[0]

    legacy_path, meta_path = get_backup_paths()
    return _decode_legacy(legacy_path, meta_path)
