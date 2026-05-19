"""Credentials load/save logic with Fernet encryption."""

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_APP_DIR = Path.home() / ".market-lens"
_CREDS_FILE = _APP_DIR / "credentials.json"
_KEY_FILE = _APP_DIR / ".key"

# Fields treated as sensitive and encrypted at rest
_SENSITIVE_FIELDS = {"api_key", "api_secret", "access_token", "password"}


def _ensure_app_dir() -> None:
    """Create ~/.market-lens directory if it does not exist."""
    _APP_DIR.mkdir(parents=True, exist_ok=True)


def _get_or_create_key() -> bytes:
    """Return the Fernet key, generating and persisting one if absent."""
    _ensure_app_dir()
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    # Restrict permissions on Unix systems
    try:
        os.chmod(_KEY_FILE, 0o600)
    except NotImplementedError:
        pass
    return key


def _fernet() -> Fernet:
    return Fernet(_get_or_create_key())


def _encrypt(value: str) -> str:
    """Encrypt a plaintext string and return base64-encoded ciphertext."""
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(token: str) -> str:
    """Decrypt a previously encrypted token."""
    return _fernet().decrypt(token.encode()).decode()


def save_credentials(source: str, fields: dict[str, str]) -> None:
    """Persist credentials for *source* to disk, encrypting sensitive fields.

    Args:
        source: The data source name (e.g. "Zerodha Kite Connect").
        fields: Mapping of field name to plaintext value.
    """
    _ensure_app_dir()
    existing = _load_raw()
    encrypted: dict[str, str] = {}
    for key, value in fields.items():
        if key in _SENSITIVE_FIELDS and value:
            encrypted[key] = _encrypt(value)
        else:
            encrypted[key] = value
    existing[source] = encrypted
    _CREDS_FILE.write_text(json.dumps(existing, indent=2))
    try:
        os.chmod(_CREDS_FILE, 0o600)
    except NotImplementedError:
        pass


def load_credentials() -> dict[str, dict[str, str]]:
    """Load and decrypt all saved credentials.

    Returns:
        Mapping of source name → field name → plaintext value.
    """
    raw = _load_raw()
    result: dict[str, dict[str, str]] = {}
    for source, fields in raw.items():
        decrypted: dict[str, str] = {}
        for key, value in fields.items():
            if key in _SENSITIVE_FIELDS and value:
                try:
                    decrypted[key] = _decrypt(value)
                except (InvalidToken, Exception):
                    decrypted[key] = ""
            else:
                decrypted[key] = value
        result[source] = decrypted
    return result


def clear_credentials(source: str | None = None) -> None:
    """Remove credentials for a specific source, or all if *source* is None.

    Args:
        source: Data source name to clear, or None to wipe everything.
    """
    if not _CREDS_FILE.exists():
        return
    if source is None:
        _CREDS_FILE.unlink()
        return
    existing = _load_raw()
    existing.pop(source, None)
    _CREDS_FILE.write_text(json.dumps(existing, indent=2))


def _load_raw() -> dict[str, Any]:
    """Return raw (still-encrypted) credentials dict from disk."""
    if not _CREDS_FILE.exists():
        return {}
    try:
        return json.loads(_CREDS_FILE.read_text())
    except json.JSONDecodeError:
        return {}
