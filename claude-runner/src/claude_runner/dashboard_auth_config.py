"""Authentication settings for the AHAWR dashboard.

Parsed from a ``Mapping[str, str]`` (the parsing function never reads
``os.environ`` directly) so the fail-closed startup rules can be unit-tested
with made-up values.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field

# Default session lifetime in seconds (30 minutes).
DEFAULT_SESSION_S = 1800


@dataclass(frozen=True)
class DashboardAuthSettings:
    """Auth-related dashboard settings parsed from environment variables."""

    bind_address: str = "127.0.0.1"
    extra_hosts: tuple[str, ...] = ()
    username: str = ""
    password_hash: str = field(default="", repr=False)
    token: str = field(default="", repr=False)
    session_secret: str = field(default="", repr=False)
    session_seconds: int = DEFAULT_SESSION_S
    readonly: bool = False
    tls_cert: str | None = None
    tls_key: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> DashboardAuthSettings:
        """Build settings from an environment mapping (no os.environ access)."""
        extra_hosts = tuple(
            host.strip() for host in env.get("DASHBOARD_HOSTS_EXTRA", "").split(",") if host.strip()
        )
        return cls(
            bind_address=env.get("AHAWR_DASHBOARD_BIND", "127.0.0.1").strip(),
            extra_hosts=extra_hosts,
            username=env.get("DASHBOARD_USER", "").strip(),
            password_hash=env.get("DASHBOARD_PASSWORD_HASH", "").strip(),
            token=env.get("DASHBOARD_TOKEN", "").strip(),
            session_secret=env.get("DASHBOARD_SESSION_SECRET", "").strip(),
            session_seconds=int(env.get("DASHBOARD_SESSION_SECONDS", str(DEFAULT_SESSION_S))),
            readonly=env.get("DASHBOARD_READONLY", "").strip() in ("1", "true"),
            tls_cert=env.get("DASHBOARD_TLS_CERT", "").strip() or None,
            tls_key=env.get("DASHBOARD_TLS_KEY", "").strip() or None,
        )


def is_loopback(host: str) -> bool:
    """Return True when *host* is a loopback address or the name 'localhost'."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def auth_enabled(settings: DashboardAuthSettings) -> bool:
    """Return True when token auth or username+password auth is configured."""
    return bool(settings.token) or (bool(settings.username) and bool(settings.password_hash))


def validate_startup(settings: DashboardAuthSettings, bind_host: str) -> None:
    """Fail-closed startup validation for the dashboard.

    Raises ValueError when the configuration is unsafe:
    - a TLS certificate without a key;
    - password login (a hash) without a session secret;
    - a username without a password hash;
    - a non-loopback bind host with no authentication.
    """
    if settings.tls_cert is not None and settings.tls_key is None:
        raise ValueError(
            "DASHBOARD_TLS_CERT is set but DASHBOARD_TLS_KEY is missing: "
            "a TLS key is required with a certificate."
        )

    if settings.password_hash and not settings.session_secret:
        raise ValueError(
            "DASHBOARD_PASSWORD_HASH is set but DASHBOARD_SESSION_SECRET is "
            "missing: a session secret is required for password login."
        )

    if settings.username and not settings.password_hash:
        raise ValueError(
            "DASHBOARD_USER is set but DASHBOARD_PASSWORD_HASH is missing: "
            "a password hash is required with a username."
        )

    if not is_loopback(bind_host) and not auth_enabled(settings):
        raise ValueError(
            "Binding to a non-loopback host requires authentication: "
            "set DASHBOARD_TOKEN, or set DASHBOARD_USER with "
            "DASHBOARD_PASSWORD_HASH and DASHBOARD_SESSION_SECRET, "
            "or bind to a loopback address."
        )
