import dataclasses
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

import pytest

from claude_runner import provider_secrets
from claude_runner.provider_secrets import (
    KEY_ENV_NAME,
    ProviderSecrets,
    SecretsError,
    SecretsKeyError,
    redact,
)

PROVIDER = "made-up-provider"
KEY = "sk-test-0000-made-up-key"
OTHER_KEY = "sk-test-1111-made-up-key"
POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX file modes are not enforced on Windows"
)
HAS_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None
NEEDS_CRYPTOGRAPHY = pytest.mark.skipif(
    not HAS_CRYPTOGRAPHY, reason="cryptography is not installed"
)


def _store(tmp_path: Path) -> ProviderSecrets:
    return ProviderSecrets(tmp_path / "secrets.json")


def _encrypted_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, master_key: str
) -> ProviderSecrets:
    (tmp_path / "crypto.txt").write_text("encrypted\n", encoding="utf-8")
    monkeypatch.setenv(KEY_ENV_NAME, master_key)
    return _store(tmp_path)


@POSIX_ONLY
def test_secrets_file_is_mode_0600(tmp_path: Path) -> None:
    _store(tmp_path).set_key(PROVIDER, KEY)
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600


def test_plain_file_is_json_when_no_crypto_marker(tmp_path: Path) -> None:
    _store(tmp_path).set_key(PROVIDER, KEY)
    data = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert data[PROVIDER]["key"] == KEY


def test_get_and_delete_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_key(PROVIDER, KEY)
    assert store.get_key(PROVIDER) == KEY
    store.delete_key(PROVIDER)
    assert store.get_key(PROVIDER) is None
    assert store.key_state(PROVIDER).has_key is False


def test_key_state_reports_set_at_and_hides_the_value(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_key(PROVIDER, KEY)
    state = store.key_state(PROVIDER)
    assert state.has_key is True
    assert state.set_at is not None
    assert [field.name for field in dataclasses.fields(state)] == ["has_key", "set_at"]
    assert KEY not in repr(state)
    assert KEY not in str(dataclasses.asdict(state))


def test_empty_key_is_rejected_without_writing(tmp_path: Path) -> None:
    with pytest.raises(SecretsError):
        _store(tmp_path).set_key(PROVIDER, "")
    assert not (tmp_path / "secrets.json").exists()


def test_failed_write_keeps_the_old_file_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.set_key(PROVIDER, KEY)
    before = (tmp_path / "secrets.json").read_bytes()

    def failing_replace(source: str, target: str) -> None:
        raise OSError("simulated failure during rename")

    monkeypatch.setattr(provider_secrets.os, "replace", failing_replace)
    with pytest.raises(OSError, match="simulated failure"):
        store.set_key(PROVIDER, OTHER_KEY)
    monkeypatch.undo()

    assert (tmp_path / "secrets.json").read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["secrets.json"]
    assert store.get_key(PROVIDER) == KEY


def test_redact_replaces_every_secret_and_ignores_empty_values() -> None:
    text = f"header {KEY} and again {KEY}, short {OTHER_KEY[:9]}"
    assert (
        redact(text, [KEY, "", OTHER_KEY[:9]])
        == "header [REDACTED] and again [REDACTED], short [REDACTED]"
    )
    assert redact("nothing here", [""]) == "nothing here"


def test_log_filter_redacts_a_key_in_a_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    store.set_key(PROVIDER, KEY)
    caplog.handler.addFilter(store.log_filter())
    caplog.set_level(logging.DEBUG)
    logging.getLogger("claude_runner.test_provider_secrets").warning("request failed with %s", KEY)
    assert "request failed with [REDACTED]" in caplog.text
    assert KEY not in caplog.text


def test_no_secret_appears_in_any_log_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    store = _store(tmp_path)
    caplog.handler.addFilter(store.log_filter())
    store.set_key(PROVIDER, KEY)
    store.get_key(PROVIDER)
    store.key_state(PROVIDER)
    store.set_key(PROVIDER, OTHER_KEY)
    store.delete_key(PROVIDER)
    assert caplog.records
    assert KEY not in caplog.text
    assert OTHER_KEY not in caplog.text


@POSIX_ONLY
def test_wider_file_mode_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_key(PROVIDER, KEY)
    os.chmod(tmp_path / "secrets.json", 0o644)
    with pytest.raises(SecretsError, match="wider than 0600"):
        store.get_key(PROVIDER)


def test_missing_master_key_raises_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "crypto.txt").write_text("encrypted\n", encoding="utf-8")
    (tmp_path / "secrets.json").write_bytes(b"not read without the key")
    os.chmod(tmp_path / "secrets.json", 0o600)
    monkeypatch.delenv(KEY_ENV_NAME, raising=False)
    with pytest.raises(SecretsKeyError, match=KEY_ENV_NAME) as error:
        _store(tmp_path).get_key(PROVIDER)
    assert KEY not in str(error.value)


@NEEDS_CRYPTOGRAPHY
def test_encrypted_round_trip_keeps_the_value_out_of_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    master_key = Fernet.generate_key().decode("ascii")
    store = _encrypted_store(tmp_path, monkeypatch, master_key)
    store.set_key(PROVIDER, KEY)
    assert KEY.encode() not in (tmp_path / "secrets.json").read_bytes()
    assert store.get_key(PROVIDER) == KEY


@NEEDS_CRYPTOGRAPHY
def test_wrong_master_key_raises_clean_error_without_key_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    right_key = Fernet.generate_key().decode("ascii")
    wrong_key = Fernet.generate_key().decode("ascii")
    _encrypted_store(tmp_path, monkeypatch, right_key).set_key(PROVIDER, KEY)
    monkeypatch.setenv(KEY_ENV_NAME, wrong_key)
    with pytest.raises(SecretsKeyError) as error:
        _store(tmp_path).get_key(PROVIDER)
    message = str(error.value)
    assert right_key not in message
    assert wrong_key not in message
    assert KEY not in message


@NEEDS_CRYPTOGRAPHY
def test_malformed_master_key_raises_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _encrypted_store(tmp_path, monkeypatch, "not-a-fernet-key")
    with pytest.raises(SecretsKeyError, match="not a valid Fernet key") as error:
        store.set_key(PROVIDER, KEY)
    assert "not-a-fernet-key" not in str(error.value)
