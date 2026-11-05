# Dashboard on the LAN

This guide shows how to reach the AHAWR dashboard (`ahawr-dashboard`, port 8701) from a phone or another PC on
the local network. By default the dashboard listens on loopback only and needs no login. Opening it to the LAN
needs a password or token, a session secret, and, strongly recommended, HTTPS.

Placeholders are in UPPER CASE. Replace them on the host only. Never commit a real value, and never paste a real
hash, token, secret, key or certificate into a document, an issue or a chat.

## 1. Variables and safe defaults

Defaults are taken from `docker-compose.yml` (service `ahawr-dashboard`, lines 329-339) and from
`ahawr-runner/src/ahawr_runner/dashboard_auth_config.py`.

| Variable | Default | Meaning |
|---|---|---|
| `DASHBOARD_LAN_PORT` | empty | Container port of the second, LAN listener (use `8702`). Empty = no LAN listener. It needs the login below and is read-only when `DASHBOARD_READONLY=1`. |
| `AHAWR_DASHBOARD_LAN_BIND` | `127.0.0.1` | Host address the LAN port is published on. Loopback = this PC only. |
| `AHAWR_DASHBOARD_LAN_PORT` | `8702` | Host port of the LAN listener. |
| `AHAWR_DASHBOARD_PORT` | `8701` | Open, writable port for the operator's tools; always published on `127.0.0.1`. |
| `DASHBOARD_USER` | empty | Login name for password login. Needs a hash. |
| `DASHBOARD_PASSWORD_HASH` | empty | scrypt hash of the password (section 2). Needs a session secret. |
| `DASHBOARD_SESSION_SECRET` | empty | Key that signs session cookies. Needs to be set with the hash. |
| `DASHBOARD_TOKEN` | empty | Bearer token for scripts and the login form. Grants full access. |
| `DASHBOARD_READONLY` | `0` | `1` makes the LAN port read-only: every write gets 403 and the write controls are hidden (section 4). |
| `DASHBOARD_HOSTS_EXTRA` | empty | Extra host names accepted in the `Host` header, comma-separated. |
| `DASHBOARD_TLS_CERT` | empty | Path inside the container to the certificate (section 5). |
| `DASHBOARD_TLS_KEY` | empty | Path inside the container to the private key (section 5). |

Fixed in code, not in `.env`: the session lifetime is 1800 seconds (`DEFAULT_SESSION_S`,
`dashboard_auth_config.py:11`). `DASHBOARD_SESSION_SECONDS` is read by the code, but compose does not pass it
into the container, so setting it in `.env` has no effect today.

Startup refuses unsafe combinations (fail closed), with a message that names the variables
(`dashboard_auth_config.py:68-101`):
- a certificate without a key, or a hash without a session secret, or a user without a hash;
- a non-loopback bind with no authentication: `test_validate_non_loopback_without_auth_raises`
  (`tests/test_dashboard_auth_config.py:141`).

## 2. Password hash and session secret

Hash the password with the console script `ahawr-dashboard-hash-password`
(`ahawr-runner/pyproject.toml:29`). It reads one line from stdin, refuses an empty password, and prints the hash
(`dashboard_auth_crypto.py:171-183`). Use stdin, never a command-line argument, so the password does not reach the
shell history:

```
docker compose run --rm -i ahawr-dashboard ahawr-dashboard-hash-password
```

Type the password and press Enter. Copy the single output line into `.env`. The hash has this form
(`dashboard_auth_crypto.py:6`), with a random salt per hash (`test_two_hashes_of_same_password_differ`):

```
scrypt$N_COST$R_BLOCK$P_PARALLEL$SALT_BASE64$HASH_BASE64
```

Generate the session secret and the token the same way (Python 3, standard library only):

```
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Run it twice: once for `DASHBOARD_SESSION_SECRET`, once for `DASHBOARD_TOKEN`. Keep them different.

## 3. Example `.env` lines

Put these in the repository-root `.env`, which git ignores (`.gitignore:1-2`). Values in upper case are
placeholders:

```
DASHBOARD_LAN_PORT=8702
AHAWR_DASHBOARD_LAN_BIND=LAN_IP_OF_THIS_PC
DASHBOARD_USER=LOGIN_NAME_HERE
DASHBOARD_PASSWORD_HASH=PASTE_HASH_LINE_HERE
DASHBOARD_SESSION_SECRET=PASTE_SESSION_SECRET_HERE
DASHBOARD_TOKEN=PASTE_TOKEN_HERE
DASHBOARD_READONLY=1
```

`AHAWR_DASHBOARD_LAN_BIND=0.0.0.0` publishes on every interface, including Wi-Fi networks you did not mean to open.
Use the LAN address of this PC where possible. Leave `DASHBOARD_TOKEN` empty if you only want the password.

## 4. Read-only mode for the network

Set `DASHBOARD_READONLY=1` for the LAN port. Every write request that arrives on that port (POST, PUT, PATCH,
DELETE) gets a 403 "read-only" answer, and `GET /api/ui-mode` tells the page to hide the write controls
(`dashboard_auth.py`, `board_readonly.js`; tests in `tests/test_dashboard_auth.py` and `tests/test_dashboard_security.py`).
The loopback port (8701) is not affected: the operator's tools write through it. It is published on `127.0.0.1` only.

## 5. HTTPS with a self-signed LAN certificate

Without HTTPS, the password, the token and the session cookie cross the LAN in clear text (section 7). A self-signed
certificate is enough for a home LAN. Create it outside the repository, with the LAN IP and the host name in the
subject alternative name (SAN). Replace the placeholders:

```
openssl req -x509 -newkey rsa:2048 -sha256 -days 365 -nodes \
  -keyout "/HOST_PATH_OUTSIDE_REPO/ahawr-lan.key" \
  -out "/HOST_PATH_OUTSIDE_REPO/ahawr-lan.crt" \
  -subj "/CN=LAN_HOST_NAME_HERE" \
  -addext "subjectAltName=DNS:LAN_HOST_NAME_HERE,IP:LAN_IP_HERE" \
  -addext "basicConstraints=critical,CA:TRUE"
```

- Keep both files outside the repository and out of backups you share. Restrict the key to your account
  (`chmod 600` on Linux or WSL; on Windows, the folder ACL).
- `docker-compose.yml:390-394` holds a commented, inactive example bind: the host folder `D:\n8n\certs`
  mounted at `/certs`, read-only. The operator uncomments it (or adds the same bind in a compose override), puts
  `lan.crt` and `lan.key` in that folder, and sets `DASHBOARD_TLS_CERT=/certs/lan.crt` and
  `DASHBOARD_TLS_KEY=/certs/lan.key`. The certificate folder must stay outside the repository; the example uses
  a host path for that reason. The path check and the uvicorn arguments are in `dashboard.py:968-988`, tested by
  `test_main_passes_ssl_kwargs_when_cert_and_key_set`.
- With HTTPS on, the cookie gets the `Secure` flag (`dashboard_auth_crypto.py:166-167`, test
  `test_cookie_attributes_has_secure_with_https`).

Install the certificate as trusted on the phone. Copy only the `.crt` file to the phone. The key stays on the PC.

- Android: Settings, Security and privacy (the name varies), Encryption and credentials, Install a certificate,
  CA certificate. Accept the warning. Then open `https://LAN_HOST_NAME_HERE:8701`.
- iPhone or iPad: open the `.crt` file (AirDrop or mail), allow the profile download, then Settings, General,
  VPN and Device Management, install the profile. Then Settings, General, About, Certificate Trust Settings, and
  turn full trust on for the certificate. Without the last step, Safari still refuses the page.

## 6. Windows firewall (Private profile only, not executed by this mission)

The following command is given for the operator to read. The mission did NOT run it, and no firewall rule was
changed. If you want the rule, run it yourself in an elevated PowerShell:

```
New-NetFirewallRule -DisplayName 'AHAWR dashboard (private)' -Direction Inbound -Protocol TCP -LocalPort 8701 -Profile Private -Action Allow
```

`-Profile Private` limits the rule to networks that Windows marks as private. Do not change it to Any or Public.

## 7. Threat model

What the design protects, with the test or code that checks it:

- Passwords, tokens, cookies and `Authorization` headers are not logged (only the client address and a generic
  word): `test_no_secrets_in_logs` (`tests/test_dashboard_auth.py:222`), `test_caplog_has_no_password_or_token`
  (`tests/test_dashboard_security.py:285`), and the logger calls in `dashboard_auth.py:136-200`.
- Responses do not contain the token or the hash: `test_response_bodies_have_no_token_or_password_hash`
  (`tests/test_dashboard_security.py:298`).
- The password is stored only as a salted scrypt hash (`dashboard_auth_crypto.py:22-25, 33-44`;
  `test_hash_format_is_self_describing`, `tests/test_dashboard_auth_crypto.py:41`).
- Token and hash comparisons are constant time: `test_verify_uses_compare_digest` and
  `test_check_token_uses_compare_digest` (`tests/test_dashboard_auth_crypto.py:113, 129`).
- Session cookies are HMAC-signed. Tampering, another secret, and expiry are refused:
  `test_tampered_session_cookie_is_refused`, `test_session_signed_with_another_secret_is_refused`,
  `test_expired_session_cookie_is_refused` (`tests/test_dashboard_security.py:181, 188, 194`). Changing
  `DASHBOARD_SESSION_SECRET` logs everyone out.
- The cookie is `HttpOnly`, `SameSite=Strict`, `Path=/`, with a `Max-Age`
  (`dashboard_auth_crypto.py:165`; `test_cookie_attributes_has_httponly_samesite_path_maxage`,
  `tests/test_dashboard_auth_crypto.py:262`).
- Login is rate limited per client address: 5 free failures, then an exponential lockout up to 3600 s
  (`dashboard_auth_limit.py:18-28`; `test_repeated_failures_gives_429`, `tests/test_dashboard_auth.py:204`).
- Only the expected `Host` names are accepted (`TrustedHostMiddleware`, `dashboard.py:202`;
  `test_foreign_host_header_is_rejected_even_with_valid_bearer`, `tests/test_dashboard_security.py:219`).
- Mutations under `/api/board/` and `/api/host-actions/`, and the cancel route, need the header
  `X-AHAWR-Dashboard: 1` (`dashboard.py:213-223` and `:547`; `test_dashboard_header_is_required_on_every_mutating_route`,
  `tests/test_dashboard_security.py:233`).
- Every route is either public or refuses unauthenticated requests
  (`test_every_route_is_public_or_refuses_unauthenticated_requests`, `tests/test_dashboard_security.py:267`).
  The public list is `dashboard_auth.py:52-62`.

What it does not protect:

- Plain HTTP on the LAN: the password, the token and the session cookie are readable by any device on the same
  network. The cookie has no `Secure` flag without HTTPS
  (`test_cookie_attributes_no_secure_without_https`, `tests/test_dashboard_auth_crypto.py:270`). Use section 5.
- CSRF: there is no CSRF token. Protection is `SameSite=Strict` plus the `X-AHAWR-Dashboard` header on
  mutations. A header cannot be sent cross-site by a plain form, but this is the only defence.
- Multiple users: there is one username, one password hash and one token. Anyone with the token has full access.
- Audit: there is no audit log. Login outcomes are logged as address plus a word (`dashboard_auth.py:184, 188`),
  without a user name per action.
- Limiter state is in memory. It lives in the process (`dashboard.py:227`, `LoginLimiter()`), so a restart clears
  all lockouts, and it tracks at most 1000 addresses (`dashboard_auth_limit.py:26`).
- Read-only mode blocks only Stop (section 4).
- Session lifetime is fixed at 1800 seconds in this build (section 1).

## 8. Apply the change

Only after the `.env` values and any certificate are in place, the operator rebuilds and recreates the container.
This mission did not run these commands:

```
docker compose up -d --build ahawr-dashboard
```

Then check the health endpoint on the PC (`http://127.0.0.1:8701/healthz`, or the HTTPS URL if you use TLS, with
the trusted certificate) and log in from the phone.

## 9. Manual phone check

Run these on a real phone, on the LAN, after section 5. Automated checks in
`ahawr-runner/tests/test_dashboard_mobile.py` cover the markup only, so the phone check is what proves the layout.

1. Width 360 to 430 px: the header, the run list and the tabs fit without sideways scrolling. Code and `pre`
   blocks scroll on their own (`test_dashboard_mobile_pre_and_code_scroll_horizontally`).
2. Rotate the phone: the layout reflows and the view stays usable (`test_dashboard_view_listens_for_media_changes`).
3. View switch: cycle auto, mobile and desktop. Each forced mode shows its layout
   (`test_dashboard_view_switch_cycles_auto_mobile_desktop`).
4. Touch targets: buttons are easy to tap, at least 44 px on coarse pointers
   (`test_dashboard_coarse_pointer_rule`, `test_dashboard_mobile_touch_targets_are_44px`).
5. Stop: tap Stop on a running run, and check that a confirmation appears before anything is sent
   (`test_dashboard_stop_run_asks_for_confirmation`). In read-only mode the Stop is refused.
6. Timeline and back button: open a timeline full-screen panel and use the back button
   (`test_dashboard_mobile_timeline_panel_is_full_screen`, `test_dashboard_mobile_panel_has_back_button`).
7. Login page: the form fits the screen and its buttons are at least 44 px
   (`test_login_has_viewport_meta`, `test_login_touch_targets_are_44px`).
8. Theme: switch the theme on the dashboard and on the login page. Both stay readable.
9. Lock and unlock the phone, and check that the session still works within its lifetime, then that it asks for
   the password again after it ends.

## 10. Open items

- The certificate bind in `docker-compose.yml:390-394` is commented out, so HTTPS needs the operator to enable it
  (section 5). `DASHBOARD_SESSION_SECONDS` is not passed through compose, so setting it has no effect (section 1).
- The `readonly` label on the dashboard and in the README reads as a full read-only mode, but only Stop is refused
  (section 4).
