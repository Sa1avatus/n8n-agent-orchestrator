"""Tests for dashboard_auth_config."""

from __future__ import annotations

from typing import Any

import pytest

from claude_runner.dashboard_auth_config import (
    DEFAULT_SESSION_S,
    DashboardAuthSettings,
    auth_enabled,
    is_loopback,
    validate_startup,
)

# Made-up values used throughout the tests.
TOKEN = "test-token-made-up"
PASSWORD_HASH = "test-hash-made-up"
SESSION_SECRET = "test-secret-made-up"
USERNAME = "test-user"
LAN_IP = "192.168.1.10"

SECRET_VALUES = (TOKEN, PASSWORD_HASH, SESSION_SECRET)


# ---------------------------------------------------------- defaults and parsing


def test_empty_mapping_means_no_auth_no_tls_readonly_off() -> None:
    settings = DashboardAuthSettings.from_env({})
    assert settings.bind_address == "127.0.0.1"
    assert settings.extra_hosts == ()
    assert settings.username == ""
    assert settings.token == ""
    assert settings.password_hash == ""
    assert settings.session_secret == ""
    assert settings.session_seconds == DEFAULT_SESSION_S
    assert settings.readonly is False
    assert settings.tls_cert is None
    assert settings.tls_key is None
    assert auth_enabled(settings) is False


def test_extra_hosts_split_by_comma() -> None:
    settings = DashboardAuthSettings.from_env(
        {"DASHBOARD_HOSTS_EXTRA": "host1.example.com, host2.example.com"}
    )
    assert settings.extra_hosts == ("host1.example.com", "host2.example.com")


def test_bind_address_from_env() -> None:
    settings = DashboardAuthSettings.from_env({"AHAWR_DASHBOARD_BIND": "0.0.0.0"})
    assert settings.bind_address == "0.0.0.0"


def test_readonly_true_when_one() -> None:
    assert DashboardAuthSettings.from_env({"DASHBOARD_READONLY": "1"}).readonly is True


def test_readonly_false_by_default() -> None:
    assert DashboardAuthSettings.from_env({}).readonly is False


def test_session_seconds_parsed_from_env() -> None:
    settings = DashboardAuthSettings.from_env({"DASHBOARD_SESSION_SECONDS": "3600"})
    assert settings.session_seconds == 3600


def test_tls_paths_from_env() -> None:
    settings = DashboardAuthSettings.from_env(
        {"DASHBOARD_TLS_CERT": "/fake/cert.pem", "DASHBOARD_TLS_KEY": "/fake/key.pem"}
    )
    assert settings.tls_cert == "/fake/cert.pem"
    assert settings.tls_key == "/fake/key.pem"


# ---------------------------------------------------------- is_loopback


def test_is_loopback_localhost() -> None:
    assert is_loopback("localhost")
    assert is_loopback("LOCALHOST")


def test_is_loopback_ipv4() -> None:
    assert is_loopback("127.0.0.1")
    assert is_loopback("127.0.0.2")
    assert is_loopback("127.255.255.255")


def test_is_loopback_ipv6() -> None:
    assert is_loopback("::1")


def test_is_loopback_false_for_non_loopback() -> None:
    assert not is_loopback("0.0.0.0")
    assert not is_loopback(LAN_IP)
    assert not is_loopback("10.0.0.1")


def test_is_loopback_false_for_unknown_names() -> None:
    assert not is_loopback("example.com")


# ---------------------------------------------------------- auth_enabled


def test_auth_enabled_with_token() -> None:
    assert auth_enabled(DashboardAuthSettings(token=TOKEN))


def test_auth_enabled_with_user_and_hash() -> None:
    settings = DashboardAuthSettings(
        username=USERNAME, password_hash=PASSWORD_HASH, session_secret=SESSION_SECRET
    )
    assert auth_enabled(settings)


def test_auth_enabled_false_for_user_without_hash() -> None:
    assert not auth_enabled(DashboardAuthSettings(username=USERNAME))


def test_auth_enabled_false_for_hash_without_user() -> None:
    assert not auth_enabled(DashboardAuthSettings(password_hash=PASSWORD_HASH))


def test_auth_enabled_false_for_empty() -> None:
    assert not auth_enabled(DashboardAuthSettings())


# ---------------------------------------------------------- validate_startup: bind host


def test_validate_loopback_without_auth_passes() -> None:
    settings = DashboardAuthSettings.from_env({})
    validate_startup(settings, "127.0.0.1")
    validate_startup(settings, "localhost")


def test_validate_non_loopback_without_auth_raises() -> None:
    settings = DashboardAuthSettings.from_env({})
    with pytest.raises(ValueError, match="DASHBOARD_TOKEN"):
        validate_startup(settings, "0.0.0.0")
    with pytest.raises(ValueError, match="DASHBOARD_TOKEN"):
        validate_startup(settings, LAN_IP)


def test_validate_non_loopback_with_token_passes() -> None:
    settings = DashboardAuthSettings.from_env({"DASHBOARD_TOKEN": TOKEN})
    validate_startup(settings, "0.0.0.0")


def test_validate_non_loopback_with_user_hash_and_secret_passes() -> None:
    env = {
        "DASHBOARD_USER": USERNAME,
        "DASHBOARD_PASSWORD_HASH": PASSWORD_HASH,
        "DASHBOARD_SESSION_SECRET": SESSION_SECRET,
    }
    settings = DashboardAuthSettings.from_env(env)
    validate_startup(settings, "0.0.0.0")


def test_validate_bind_host_error_message_names_all_vars() -> None:
    settings = DashboardAuthSettings.from_env({})
    try:
        validate_startup(settings, "0.0.0.0")
    except ValueError as exc:
        message = str(exc)
        assert "DASHBOARD_TOKEN" in message
        assert "DASHBOARD_USER" in message
        assert "DASHBOARD_PASSWORD_HASH" in message
        assert "DASHBOARD_SESSION_SECRET" in message
    else:
        pytest.fail("Expected ValueError")


# ---------------------------------------------------------- validate_startup: config errors


def test_validate_tls_cert_without_key_raises() -> None:
    settings = DashboardAuthSettings.from_env({"DASHBOARD_TLS_CERT": "/fake/cert.pem"})
    with pytest.raises(ValueError, match="DASHBOARD_TLS_KEY"):
        validate_startup(settings, "127.0.0.1")


def test_validate_tls_cert_and_key_passes_tls_check() -> None:
    settings = DashboardAuthSettings.from_env(
        {"DASHBOARD_TLS_CERT": "/fake/cert.pem", "DASHBOARD_TLS_KEY": "/fake/key.pem"}
    )
    validate_startup(settings, "127.0.0.1")


def test_validate_password_hash_without_secret_raises() -> None:
    settings = DashboardAuthSettings.from_env({"DASHBOARD_PASSWORD_HASH": PASSWORD_HASH})
    with pytest.raises(ValueError, match="DASHBOARD_SESSION_SECRET"):
        validate_startup(settings, "127.0.0.1")


def test_validate_user_without_hash_raises() -> None:
    settings = DashboardAuthSettings.from_env({"DASHBOARD_USER": USERNAME})
    with pytest.raises(ValueError, match="DASHBOARD_PASSWORD_HASH"):
        validate_startup(settings, "127.0.0.1")


def test_validate_user_with_hash_without_secret_raises() -> None:
    env = {
        "DASHBOARD_USER": USERNAME,
        "DASHBOARD_PASSWORD_HASH": PASSWORD_HASH,
    }
    settings = DashboardAuthSettings.from_env(env)
    with pytest.raises(ValueError, match="DASHBOARD_SESSION_SECRET"):
        validate_startup(settings, "127.0.0.1")


# ---------------------------------------------------------- secrets in repr


def test_repr_hides_token_value() -> None:
    settings = DashboardAuthSettings(token=TOKEN)
    assert TOKEN not in repr(settings)


def test_repr_hides_password_hash_value() -> None:
    settings = DashboardAuthSettings(password_hash=PASSWORD_HASH)
    assert PASSWORD_HASH not in repr(settings)


def test_repr_hides_session_secret_value() -> None:
    settings = DashboardAuthSettings(session_secret=SESSION_SECRET)
    assert SESSION_SECRET not in repr(settings)


def test_repr_from_env_hides_all_secrets() -> None:
    env = {
        "DASHBOARD_TOKEN": TOKEN,
        "DASHBOARD_PASSWORD_HASH": PASSWORD_HASH,
        "DASHBOARD_SESSION_SECRET": SESSION_SECRET,
        "DASHBOARD_USER": USERNAME,
    }
    settings = DashboardAuthSettings.from_env(env)
    result = repr(settings)
    for secret in SECRET_VALUES:
        assert secret not in result


def test_repr_still_shows_non_secret_fields() -> None:
    settings = DashboardAuthSettings(
        bind_address="0.0.0.0",
        username=USERNAME,
        token=TOKEN,
        readonly=True,
    )
    result = repr(settings)
    assert "bind_address='0.0.0.0'" in result
    assert "username='test-user'" in result
    assert "readonly=True" in result


# ---------------------------------------------------------- secrets in errors


def test_error_messages_never_contain_secret_values() -> None:
    """Every code path that raises must omit secret *values* (only var names)."""
    configs: list[dict[str, str]] = [
        {"DASHBOARD_TOKEN": TOKEN},
        {"DASHBOARD_PASSWORD_HASH": PASSWORD_HASH},
        {"DASHBOARD_SESSION_SECRET": SESSION_SECRET},
        {
            "DASHBOARD_USER": USERNAME,
            "DASHBOARD_PASSWORD_HASH": PASSWORD_HASH,
            "DASHBOARD_SESSION_SECRET": SESSION_SECRET,
        },
        {
            "AHAWR_DASHBOARD_BIND": "0.0.0.0",
            "DASHBOARD_TOKEN": TOKEN,
            "DASHBOARD_PASSWORD_HASH": PASSWORD_HASH,
            "DASHBOARD_SESSION_SECRET": SESSION_SECRET,
        },
        {"DASHBOARD_TLS_CERT": "/fake/cert.pem", "DASHBOARD_TLS_KEY": "/fake/key.pem"},
    ]
    for config in configs:
        settings = DashboardAuthSettings.from_env(config)
        for host in ["0.0.0.0", LAN_IP, "127.0.0.1", "localhost", "::1"]:
            try:
                validate_startup(settings, host)
            except ValueError as exc:
                message = str(exc)
                for secret in SECRET_VALUES:
                    assert secret not in message, (
                        f"secret value {secret!r} leaked in error for "
                        f"config={config} host={host}: {message}"
                    )
            except Exception:
                pass


# ---------------------------------------------------------- main() CLI: validate_startup + TLS


def test_main_rejects_non_loopback_bind_without_auth(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-loopback published bind without auth exits non-zero,
    uvicorn.run is not called, and stderr names the required variables."""
    import uvicorn

    from claude_runner.dashboard import main as dashboard_main

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: called.append(kw))
    monkeypatch.setenv("AHAWR_DASHBOARD_BIND", "0.0.0.0")
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)

    code = dashboard_main(["--port", "9801"])
    assert code != 0
    assert called == []
    err = capsys.readouterr().err
    assert "DASHBOARD_TOKEN" in err
    assert "DASHBOARD_USER" in err
    assert "DASHBOARD_PASSWORD_HASH" in err
    assert "DASHBOARD_SESSION_SECRET" in err


def test_main_calls_uvicorn_with_non_loopback_bind_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-loopback bind with a token configured passes validation
    and calls uvicorn.run."""
    import uvicorn

    from claude_runner.dashboard import main as dashboard_main

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("AHAWR_DASHBOARD_BIND", "0.0.0.0")
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)

    assert dashboard_main(["--port", "9802"]) == 0
    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 9802


def test_main_passes_ssl_kwargs_when_cert_and_key_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """With TLS cert and key set to real temp files, uvicorn.run receives
    ssl_certfile and ssl_keyfile."""
    import uvicorn

    from claude_runner.dashboard import main as dashboard_main

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("fake cert")
    key.write_text("fake key")

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("DASHBOARD_TLS_CERT", str(cert))
    monkeypatch.setenv("DASHBOARD_TLS_KEY", str(key))
    monkeypatch.delenv("AHAWR_DASHBOARD_BIND", raising=False)

    assert dashboard_main(["--host", "127.0.0.1", "--port", "9803"]) == 0
    assert seen["ssl_certfile"] == str(cert)
    assert seen["ssl_keyfile"] == str(key)
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 9803


def test_main_fails_when_tls_files_do_not_exist(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When TLS cert/key paths do not point to real files, main exits
    non-zero and uvicorn.run is not called."""
    import uvicorn

    from claude_runner.dashboard import main as dashboard_main

    called: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: called.append(kw))
    monkeypatch.setenv("DASHBOARD_TLS_CERT", "/nonexistent/cert.pem")
    monkeypatch.setenv("DASHBOARD_TLS_KEY", "/nonexistent/key.pem")
    monkeypatch.delenv("AHAWR_DASHBOARD_BIND", raising=False)

    code = dashboard_main(["--host", "127.0.0.1", "--port", "9804"])
    assert code != 0
    assert called == []
    err = capsys.readouterr().err
    assert "DASHBOARD_TLS_CERT" in err or "DASHBOARD_TLS_KEY" in err


def test_main_no_settings_same_args_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no auth and no TLS on a loopback bind, uvicorn.run gets the
    same arguments as before the change (no ssl_* keys)."""
    import uvicorn

    from claude_runner.dashboard import main as dashboard_main

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.delenv("AHAWR_DASHBOARD_BIND", raising=False)
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_TLS_CERT", raising=False)
    monkeypatch.delenv("DASHBOARD_TLS_KEY", raising=False)

    assert dashboard_main(["--host", "127.0.0.1", "--port", "9805"]) == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 9805
    assert seen["log_level"] == "warning"
    assert "ssl_certfile" not in seen
    assert "ssl_keyfile" not in seen


def test_main_default_container_launch_starts_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default container launch (--host 0.0.0.0, AHAWR_DASHBOARD_BIND
    unset) publishes on loopback, so it starts without auth."""
    import uvicorn

    from claude_runner.dashboard import main as dashboard_main

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.delenv("AHAWR_DASHBOARD_BIND", raising=False)
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)

    assert dashboard_main(["--host", "0.0.0.0", "--port", "9806"]) == 0
    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 9806
    assert seen["log_level"] == "warning"
    assert "ssl_certfile" not in seen
    assert "ssl_keyfile" not in seen


def test_main_empty_dashboard_bind_starts_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When AHAWR_DASHBOARD_BIND is set to an empty string, the published
    bind is treated as loopback, so it starts without auth."""
    import uvicorn

    from claude_runner.dashboard import main as dashboard_main

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("AHAWR_DASHBOARD_BIND", "")
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)

    assert dashboard_main(["--host", "0.0.0.0", "--port", "9807"]) == 0
    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 9807
    assert seen["log_level"] == "warning"
    assert "ssl_certfile" not in seen
    assert "ssl_keyfile" not in seen


def test_lan_port_is_read_from_the_environment_and_off_by_default() -> None:
    assert DashboardAuthSettings.from_env({}).lan_port is None
    assert DashboardAuthSettings.from_env({"DASHBOARD_LAN_PORT": "8702"}).lan_port == 8702


def test_a_lan_port_without_authentication_refuses_to_start() -> None:
    with pytest.raises(ValueError, match="authentication"):
        validate_startup(DashboardAuthSettings(lan_port=8702), "127.0.0.1")
