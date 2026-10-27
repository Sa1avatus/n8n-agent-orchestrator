# Provider API keys: plain JSON by default, encrypted only when crypto.txt says 'encrypted'.
# File path, mode, atomic write and master key rules: claude-runner/docs/providers.md, section 10.
"""Write-only store for provider API keys, keyed by provider name."""

import importlib
import json
import logging
import os
import stat
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KEY_ENV_NAME = "CLAUDE_RUNNER_SECRETS_KEY"
CRYPTO_MARKER_NAME = "crypto.txt"
ENCRYPTED_MARKER = "encrypted"
REDACTED_TEXT = "[REDACTED]"
SECRETS_FILE_MODE = 0o600


class SecretsError(Exception):
    """A secrets file error; the message never contains a key value."""


class SecretsKeyError(SecretsError):
    """The master key is missing or does not open the secrets file."""


@dataclass(frozen=True)
class KeyState:
    has_key: bool
    set_at: str | None


def redact(text: str, secrets: Iterable[str]) -> str:
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        text = text.replace(secret, REDACTED_TEXT)
    return text


class SecretRedactionFilter(logging.Filter):
    """Redacts known key values from log messages; attach it to handlers to cover every logger."""

    def __init__(self, secret_values: Callable[[], Iterable[str]]) -> None:
        super().__init__()
        self._secret_values = secret_values

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message, self._secret_values())
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class ProviderSecrets:
    """Keys are read through get_key by the runner only; the API and the dashboard use key_state."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._seen_values: set[str] = set()

    def set_key(self, provider: str, key: str) -> None:
        if not key:
            raise SecretsError("key is empty")
        with self._lock:
            entries = self._load()
            set_at = datetime.now(UTC).isoformat(timespec="seconds")
            entries[provider] = {"key": key, "set_at": set_at}
            self._save(entries)
            self._seen_values.add(key)
        logger.info("provider key set: %s", provider)

    def delete_key(self, provider: str) -> None:
        with self._lock:
            entries = self._load()
            if entries.pop(provider, None) is None:
                return
            self._save(entries)
        logger.info("provider key deleted: %s", provider)

    def key_state(self, provider: str) -> KeyState:
        entry = self._load().get(provider)
        if entry is None:
            return KeyState(has_key=False, set_at=None)
        return KeyState(has_key=True, set_at=entry["set_at"])

    def get_key(self, provider: str) -> str | None:
        with self._lock:
            entry = self._load().get(provider)
            if entry is None:
                return None
            self._seen_values.add(entry["key"])
            return entry["key"]

    def known_values(self) -> list[str]:
        with self._lock:
            return list(self._seen_values)

    def log_filter(self) -> SecretRedactionFilter:
        return SecretRedactionFilter(self.known_values)

    def _load(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        if os.name == "posix" and stat.S_IMODE(self._path.stat().st_mode) & 0o077:
            raise SecretsError(f"secrets file mode is wider than 0600: {self._path}")
        raw = self._path.read_bytes()
        if _is_encrypted(self._path.parent):
            raw = _decrypt(raw)
        try:
            data: dict[str, dict[str, str]] = json.loads(raw)
        except json.JSONDecodeError:
            raise SecretsError(f"secrets file is not valid JSON: {self._path}") from None
        return data

    def _save(self, entries: dict[str, dict[str, str]]) -> None:
        payload = json.dumps(entries, sort_keys=True).encode("utf-8")
        if _is_encrypted(self._path.parent):
            payload = _encrypt(payload)
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=directory, prefix=self._path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, SECRETS_FILE_MODE)
            os.replace(temp_name, self._path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise


def _is_encrypted(directory: Path) -> bool:
    marker = directory / CRYPTO_MARKER_NAME
    return marker.is_file() and marker.read_text(encoding="utf-8").strip() == ENCRYPTED_MARKER


def _load_fernet() -> tuple[Any, Any]:
    key = os.environ.get(KEY_ENV_NAME, "")
    if not key:
        raise SecretsKeyError(f"{KEY_ENV_NAME} is not set")
    try:
        fernet_module: Any = importlib.import_module("cryptography.fernet")
    except ImportError:
        raise SecretsError("encrypted secrets need the cryptography package") from None
    try:
        return fernet_module, fernet_module.Fernet(key.encode("utf-8"))
    except ValueError:
        raise SecretsKeyError(f"{KEY_ENV_NAME} is not a valid Fernet key") from None


def _encrypt(payload: bytes) -> bytes:
    _, fernet = _load_fernet()
    token: bytes = fernet.encrypt(payload)
    return token


def _decrypt(raw: bytes) -> bytes:
    fernet_module, fernet = _load_fernet()
    try:
        plain: bytes = fernet.decrypt(raw)
    except fernet_module.InvalidToken:
        raise SecretsKeyError("master key does not open the secrets file") from None
    return plain
