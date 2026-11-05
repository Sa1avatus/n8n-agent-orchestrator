"""Tests for dashboard_auth_crypto."""

from __future__ import annotations

import base64
import hmac
import inspect
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import ahawr_runner.dashboard_auth_crypto as crypto
from ahawr_runner.dashboard_auth_crypto import (
    SALT_BYTES,
    SCRYPT_DKLEN,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    check_token,
    hash_password,
    issue_session,
    main,
    read_session,
    session_cookie_attributes,
    verify_password,
)

PASSWORD = "s3cr3t-Pw!"
WRONG = "wrong-password"
SECRET = "test-session-secret"
WRONG_SECRET = "wrong-secret"
SUBJECT = "user@test.com"


# ---------------------------------------------------------- hash_password


def test_hash_format_is_self_describing() -> None:
    encoded = hash_password(PASSWORD)
    parts = encoded.split("$")
    assert len(parts) == 6
    assert parts[0] == "scrypt"
    assert parts[1] == str(SCRYPT_N)
    assert parts[2] == str(SCRYPT_R)
    assert parts[3] == str(SCRYPT_P)


def test_hash_contains_salt_and_derived_of_expected_lengths() -> None:
    encoded = hash_password(PASSWORD)
    _, _, _, _, salt_b64, hash_b64 = encoded.split("$", 5)
    salt = base64.b64decode(salt_b64)
    derived = base64.b64decode(hash_b64)
    assert len(salt) == SALT_BYTES
    assert len(derived) == SCRYPT_DKLEN


# ---------------------------------------------------------- round trip


def test_verify_accepts_correct_password() -> None:
    encoded = hash_password(PASSWORD)
    assert verify_password(PASSWORD, encoded) is True


def test_verify_rejects_wrong_password() -> None:
    encoded = hash_password(PASSWORD)
    assert verify_password(WRONG, encoded) is False


def test_two_hashes_of_same_password_differ() -> None:
    """Random salt means two hashes of the same password are never equal."""
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)
    assert first != second
    # both must still verify
    assert verify_password(PASSWORD, first) is True
    assert verify_password(PASSWORD, second) is True


# ---------------------------------------------------------- malformed hashes


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "scrypt",
        "scrypt$16384$8$1",
        "scrypt$16384$8$1$salt",
        "md5$16384$8$1$salt$hash",
        "scrypt$abc$8$1$salt$hash",
        "scrypt$16384$8$1$!!!$hash",
        "scrypt$16384$8$1$salt$!!!",
        # too many fields — hash_b64 absorbs the extra $
        "scrypt$16384$8$1$salt$hash$extra",
    ],
)
def test_verify_returns_false_for_malformed_hash(malformed: str) -> None:
    assert verify_password(PASSWORD, malformed) is False


def test_verify_wrong_password_returns_false_not_raise() -> None:
    encoded = hash_password(PASSWORD)
    assert verify_password(WRONG, encoded) is False


# ---------------------------------------------------------- hmac.compare_digest usage


def test_verify_uses_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_password must compare the derived hash with hmac.compare_digest."""
    encoded = hash_password(PASSWORD)
    called: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(left: bytes, right: bytes) -> bool:
        called.append((left, right))
        return real(left, right)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    result = verify_password(PASSWORD, encoded)
    assert result is True
    assert called, "compare_digest was not called by verify_password"


def test_check_token_uses_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_token must compare tokens with hmac.compare_digest."""
    called: list[tuple[bytes, bytes]] = []

    def spy(left: bytes, right: bytes) -> bool:
        called.append((left, right))
        return True

    monkeypatch.setattr(hmac, "compare_digest", spy)
    check_token("abc", "abc")
    assert called, "compare_digest was not called by check_token"


# ---------------------------------------------------------- check_token


def test_check_token_accepts_equal() -> None:
    assert check_token("abc", "abc") is True


def test_check_token_rejects_different() -> None:
    assert check_token("abc", "xyz") is False


def test_check_token_rejects_different_length() -> None:
    assert check_token("abc", "abcd") is False


# ---------------------------------------------------------- issue_session / read_session

# Session cookie tests (issue_session, read_session, session_cookie_attributes)


def test_session_round_trip_returns_subject() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert read_session(SECRET, value, now_seconds=1000) == SUBJECT


def test_session_value_does_not_contain_secret() -> None:
    """The HMAC signature must never leak the secret into the cookie value."""
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert SECRET not in value


def test_session_value_does_not_contain_password() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert PASSWORD not in value


def test_two_sessions_with_same_params_differ() -> None:
    """Random nonce means two sessions of the same subject are never equal."""
    first = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    second = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert first != second
    assert read_session(SECRET, first, now_seconds=1000) == SUBJECT
    assert read_session(SECRET, second, now_seconds=1000) == SUBJECT


def test_read_session_rejects_wrong_secret() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert read_session(WRONG_SECRET, value, now_seconds=1000) is None


def test_read_session_rejects_one_changed_byte() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    payload, sig = value.rsplit(".", 1)
    flipped = "A" if sig[0] != "A" else "B"
    tampered = payload + "." + flipped + sig[1:]
    assert read_session(SECRET, tampered, now_seconds=1000) is None


def test_read_session_rejects_trailing_exclamation() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert read_session(SECRET, value + "!", now_seconds=1000) is None


def test_read_session_rejects_trailing_dot() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert read_session(SECRET, value + ".", now_seconds=1000) is None


def test_read_session_rejects_last_signature_char_changed() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    payload, sig = value.rsplit(".", 1)
    replacement = "A" if sig[-1] != "A" else "B"
    tampered = payload + "." + sig[:-1] + replacement
    assert read_session(SECRET, tampered, now_seconds=1000) is None


def test_read_session_rejects_expired() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert read_session(SECRET, value, now_seconds=2800) is None


def test_read_session_valid_until_expiry() -> None:
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    assert read_session(SECRET, value, now_seconds=2799) == SUBJECT


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "abc",
        "a.b.c",
        "a.b.c.d.e",
        "a.b.c.d",
        "!!.!!.!!.!!",
    ],
)
def test_read_session_rejects_malformed(malformed: str) -> None:
    assert read_session(SECRET, malformed, now_seconds=0) is None


def test_read_session_uses_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """read_session must verify the signature with hmac.compare_digest."""
    value = issue_session(SECRET, SUBJECT, now_seconds=1000, lifetime_seconds=1800)
    called: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(left: bytes, right: bytes) -> bool:
        called.append((left, right))
        return real(left, right)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    result = read_session(SECRET, value, now_seconds=1000)
    assert result == SUBJECT
    assert called, "compare_digest was not called by read_session"


# ---------------------------------------------------------- session_cookie_attributes


def test_cookie_attributes_has_httponly_samesite_path_maxage() -> None:
    attrs = session_cookie_attributes(lifetime_seconds=1800, is_https=False)
    assert "HttpOnly" in attrs
    assert "SameSite=Strict" in attrs
    assert "Path=/" in attrs
    assert "Max-Age=1800" in attrs


def test_cookie_attributes_no_secure_without_https() -> None:
    attrs = session_cookie_attributes(lifetime_seconds=1800, is_https=False)
    assert "Secure" not in attrs


def test_cookie_attributes_has_secure_with_https() -> None:
    attrs = session_cookie_attributes(lifetime_seconds=1800, is_https=True)
    assert "Secure" in attrs


def test_cookie_attributes_reflects_lifetime() -> None:
    attrs = session_cookie_attributes(lifetime_seconds=3600, is_https=True)
    assert "Max-Age=3600" in attrs


# ---------------------------------------------------------- CLI


def test_cli_reads_stdin_and_prints_matching_hash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI reads the password from stdin (not argv) and prints a hash."""
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"s3cr3t-Pw!\n")))
    rc = main()
    assert rc == 0
    out = capsys.readouterr().out.rstrip("\n")
    # the printed hash verifies against the password
    assert verify_password("s3cr3t-Pw!", out) is True
    # the password never appears in stdout
    assert "s3cr3t-Pw!" not in out


def test_main_takes_no_arguments() -> None:
    """main() has no parameter — the password comes only from stdin."""
    params = list(inspect.signature(main).parameters)
    assert params == []


def test_cli_uses_getpass_when_tty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When stdin is a TTY, getpass is used (no echo)."""
    with (
        patch("ahawr_runner.dashboard_auth_crypto.getpass", return_value=PASSWORD) as gp,
        patch.object(sys.stdin, "isatty", return_value=True),
    ):
        rc = main()
    assert rc == 0
    out = capsys.readouterr().out.rstrip("\n")
    assert verify_password(PASSWORD, out) is True
    gp.assert_called_once()


def test_cli_refuses_empty_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty password on stdin is refused with exit code 1."""
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\n")))
    rc = main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "empty password" in err


def test_cli_refuses_empty_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty stdin (no line at all) is refused."""
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"")))
    rc = main()
    assert rc == 1


# ---------------------------------------------------------- source-level grep checks


def test_source_contains_compare_digest_in_both_functions() -> None:
    """hmac.compare_digest is used in verify_password and check_token."""
    text = Path(crypto.__file__).read_text(encoding="utf-8")
    assert text.count("hmac.compare_digest") >= 2
    # verify_password compares the derived hash in constant time
    assert "hmac.compare_digest(derived, expected)" in text
    # check_token compares encoded bytes in constant time
    assert "hmac.compare_digest(given.encode" in text
