"""Password hashing and constant-time comparison helpers for the dashboard.

``hash_password`` uses ``hashlib.scrypt`` with a random per-password salt and
encodes the result as one self-describing string of the form::

    scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>

so that ``verify_password`` can re-derive the hash from the parameters stored
in the string itself.  No new dependency is required: only stdlib modules.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import sys
from getpass import getpass
from secrets import token_bytes

# scrypt cost parameters — named constants, never literals.
SCRYPT_N = 1 << 14  # CPU/memory cost; must be a power of two
SCRYPT_R = 8  # block size
SCRYPT_P = 1  # parallelisation factor
SCRYPT_DKLEN = 32  # derived key length in bytes (256 bits)
SALT_BYTES = 16  # salt length in bytes (128 bits)
_HASH_PREFIX = "scrypt"

# Session cookie parameters
SESSION_NONCE_BYTES = 16  # random nonce length in bytes (128 bits)


def hash_password(password: str) -> str:
    """Return a self-describing scrypt hash of *password* with a random salt."""
    salt = token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return _format_hash(salt, derived)


def _format_hash(salt: bytes, derived: bytes) -> str:
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(derived).decode("ascii")
    return f"{_HASH_PREFIX}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${hash_b64}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify *password* against *encoded* in constant time.

    Returns ``False`` for a wrong password or a malformed hash; it never
    raises with details that could distinguish one failure mode from another.
    """
    try:
        prefix, n_str, r_str, p_str, salt_b64, hash_b64 = encoded.split("$", 5)
    except ValueError:
        return False
    if prefix != _HASH_PREFIX:
        return False
    try:
        n = int(n_str)
        r = int(r_str)
        p = int(p_str)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
    except (ValueError, TypeError):
        return False
    if not expected:
        return False
    try:
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(derived, expected)


def check_token(given: str, expected: str) -> bool:
    """Compare two tokens in constant time using ``hmac.compare_digest``."""
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode *data* without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    """Base64url-decode *value*, restoring stripped padding.

    Strict: rejects non-alphabet characters (``validate=True``) and
    non-canonical encodings (re-encodes and compares).  Raises
    ``ValueError`` on any rejection; callers that catch ``ValueError``
    (e.g. :func:`read_session`) turn this into ``None``.
    """
    padding = "=" * (-len(value) % 4)
    translated = value.replace("-", "+").replace("_", "/")
    data = base64.b64decode(translated + padding, validate=True)
    if _b64url_encode(data) != value:
        raise ValueError("non-canonical base64url encoding")
    return data


def _sign(secret: str, payload: str) -> bytes:
    """Return the HMAC-SHA256 of *payload* under *secret*."""
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()


def issue_session(secret: str, subject: str, now_seconds: int, lifetime_seconds: int) -> str:
    """Issue a signed, base64url-encoded session cookie value.

    The value carries the *subject*, an expiry timestamp
    (``now_seconds + lifetime_seconds``) and a random nonce, all signed
    with HMAC-SHA256.
    """
    expiry = now_seconds + lifetime_seconds
    payload = (
        f"{_b64url_encode(subject.encode())}"
        f".{expiry}"
        f".{_b64url_encode(token_bytes(SESSION_NONCE_BYTES))}"
    )
    signature = _sign(secret, payload)
    return f"{payload}.{_b64url_encode(signature)}"


def read_session(secret: str, value: str, now_seconds: int) -> str | None:
    """Validate and read a subject from a signed session cookie value.

    Returns the subject for a valid, unexpired cookie, or ``None`` for
    expired, tampered or malformed values.
    """
    try:
        subject_b64, expiry_str, nonce_b64, sig_b64 = value.split(".", 3)
    except ValueError:
        return None
    payload = f"{subject_b64}.{expiry_str}.{nonce_b64}"
    try:
        expiry = int(expiry_str)
        signature = _b64url_decode(sig_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(secret, payload), signature):
        return None
    if now_seconds >= expiry:
        return None
    try:
        return _b64url_decode(subject_b64).decode("utf-8")
    except (ValueError, TypeError):
        return None


def session_cookie_attributes(lifetime_seconds: int, is_https: bool) -> str:
    """Build the Set-Cookie attribute flags for a session cookie."""
    parts = ["HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={lifetime_seconds}"]
    if is_https:
        parts.append("Secure")
    return "; ".join(parts)


def main() -> int:
    """Read a password from stdin and print its scrypt hash.

    Uses ``getpass`` when stdin is a TTY (no echo), otherwise reads one line.
    Refuses an empty password.
    """
    # getpass hides input when stdin is a TTY; otherwise read one line from pipe.
    password = getpass("Password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\r\n")
    if not password:
        print("refusing an empty password", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
