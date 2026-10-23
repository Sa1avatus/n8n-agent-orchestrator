from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("claude_runner.qa_stack")
QA_PREFIX = "qa-"
QA_DATABASE_PREFIX = "qa_"
QA_USER = "qa"
QA_PORT = 5432
PRODUCTION_HOST_NAMES = frozenset(
    {"postgres", "n8n", "ahawr", "localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
)
PRODUCTION_DATABASE_NAMES = frozenset({"postgres", "n8n", "ahawr"})
QA_POSTGRES_IMAGE = "postgres:16-alpine"
QA_FIXTURES = ("default", "empty")
STACK_MODES = ("docker", "inprocess")
DASHBOARD_FIXTURE_ENV = "DASHBOARD_QA_FIXTURE"
DASHBOARD_DSN_ENV = "DASHBOARD_BOARD_PG_DSN"
DASHBOARD_CONTAINER_PORT = 8701
CLAUDE_RUNNER_DIR = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH = CLAUDE_RUNNER_DIR / "Dockerfile"
BOARD_SCHEMA_PATH = CLAUDE_RUNNER_DIR / "docs" / "board-schema.sql"
N8N_TABLES_PATH = CLAUDE_RUNNER_DIR / "qa" / "fixtures" / "n8n_tables.json"
FIXTURE_DIR = CLAUDE_RUNNER_DIR / "qa" / "fixtures"
QA_RUNS_DIR = CLAUDE_RUNNER_DIR / "qa" / "runs"
DOCKER_TIMEOUT_SECONDS = 120
DOCKER_BUILD_TIMEOUT_SECONDS = 900
HEALTH_MAX_POLLS = 30
HEALTH_POLL_SECONDS = 2
HEALTH_REQUEST_SECONDS = 3
ERROR_TEXT_MAX_CHARS = 300
_PASSWORD_BYTES = 24
_RUN_ID_BYTES = 4
_HOST_PATTERN = re.compile(r"qa-[a-z0-9-]{1,40}")
_DATABASE_PATTERN = re.compile(r"qa_[a-z0-9_]{1,40}")
_STACK_ID_PATTERN = re.compile(r"[0-9a-f]{8}")
_PASSWORD_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_IDENTIFIER_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
MIXED_CASE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")
N8N_TABLE_PREFIX = "data_table_user_"
N8N_COLUMN_TYPES = ("integer", "double precision", "boolean", "text", "timestamp with time zone")


class QaTargetError(ValueError):
    pass


class QaStackError(RuntimeError):
    """The throwaway stack did not start; a QA run maps this to blocked."""


@dataclass
class QaStack:
    run_id: str
    host: str
    database: str
    dsn: str = field(repr=False)
    dashboard_url: str = ""
    containers: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    process: subprocess.Popen[bytes] | None = field(default=None, repr=False)


def new_qa_password() -> str:
    return secrets.token_urlsafe(_PASSWORD_BYTES)


def assert_qa_target(host: str, database: str) -> None:
    if host in PRODUCTION_HOST_NAMES:
        raise QaTargetError(f"host {host!r} is a production name, not a throwaway qa- host")
    if not host.startswith(QA_PREFIX):
        raise QaTargetError(f"host {host!r} does not carry the {QA_PREFIX!r} prefix")
    if not _HOST_PATTERN.fullmatch(host):
        raise QaTargetError(f"host {host!r} is not a valid qa- host name")
    if database in PRODUCTION_DATABASE_NAMES:
        raise QaTargetError(f"database {database!r} is a production database, not a throwaway one")
    if not database.startswith(QA_DATABASE_PREFIX):
        raise QaTargetError(
            f"database {database!r} does not carry the {QA_DATABASE_PREFIX!r} prefix"
        )
    if not _DATABASE_PATTERN.fullmatch(database):
        raise QaTargetError(f"database {database!r} is not a valid qa_ database name")


def build_qa_dsn(host: str, database: str, password: str) -> str:
    assert_qa_target(host, database)
    if not _PASSWORD_PATTERN.fullmatch(password):
        raise QaTargetError("password must be made of URL-safe characters")
    return f"postgresql://{QA_USER}:{password}@{host}:{QA_PORT}/{database}"


def stack_up(fixture: str = "default", mode: str = "docker") -> QaStack:
    if mode not in STACK_MODES:
        raise ValueError(f"mode {mode!r} is not one of {', '.join(STACK_MODES)}")
    if fixture not in QA_FIXTURES:
        raise ValueError(f"fixture {fixture!r} is not one of {', '.join(QA_FIXTURES)}")
    if mode == "inprocess":
        stack = _inprocess_up(fixture)
        _seed_verdict_files(stack.run_id, fixture)
        return stack
    sql = BOARD_SCHEMA_PATH.read_text(encoding="utf-8") + "\n" + _seed_sql(fixture)
    run_id = secrets.token_hex(_RUN_ID_BYTES)
    password = new_qa_password()
    host = f"{QA_PREFIX}{run_id}"
    database = f"{QA_DATABASE_PREFIX}{run_id}"
    dsn = build_qa_dsn(host, database, password)
    stack = QaStack(run_id=run_id, host=host, database=database, dsn=dsn)
    try:
        _start(stack, password, sql)
        _seed_verdict_files(stack.run_id, fixture)
    except Exception:
        try:
            stack_down(stack)
        except QaStackError:
            log.exception("cleanup of failed QA stack %s is incomplete", run_id)
        raise
    return stack


def stack_from_id(stack_id: str) -> QaStack:
    if not _STACK_ID_PATTERN.fullmatch(stack_id):
        raise ValueError(f"stack id {stack_id!r} is not 8 lowercase hex digits")
    host = f"{QA_PREFIX}{stack_id}"
    return QaStack(
        run_id=stack_id,
        host=host,
        database=f"{QA_DATABASE_PREFIX}{stack_id}",
        dsn="",
        containers=[host, f"{host}-dashboard"],
        volumes=[f"{host}-data"],
        networks=[f"{host}-net"],
        images=[f"{QA_PREFIX}dashboard:{stack_id}"],
    )


def stack_down(stack: QaStack) -> None:
    _remove_seeded_runs(stack.run_id)
    if stack.process is not None:
        _kill_tree(stack.process)
        return
    failures: list[str] = []
    for command in _teardown_commands(stack):
        if not _present(command[-1]):
            continue
        try:
            _docker_checked(command, DOCKER_TIMEOUT_SECONDS)
        except QaStackError as error:
            failures.append(str(error))
    if failures:
        raise QaStackError(f"teardown of QA stack {stack.run_id} incomplete: {'; '.join(failures)}")


def _start(stack: QaStack, password: str, sql: str) -> None:
    image = f"{QA_PREFIX}dashboard:{stack.run_id}"
    network = f"{QA_PREFIX}{stack.run_id}-net"
    volume = f"{QA_PREFIX}{stack.run_id}-data"
    _docker_checked(
        ["build", "-t", image, "-f", str(DOCKERFILE_PATH), str(CLAUDE_RUNNER_DIR)],
        DOCKER_BUILD_TIMEOUT_SECONDS,
    )
    stack.images.append(image)
    _docker_checked(["network", "create", network], DOCKER_TIMEOUT_SECONDS)
    stack.networks.append(network)
    _docker_checked(["volume", "create", volume], DOCKER_TIMEOUT_SECONDS)
    stack.volumes.append(volume)
    _start_postgres(stack, password, network, volume)
    _docker_checked(
        ["run", "--rm", "-i", "--network", network, QA_POSTGRES_IMAGE,
         "psql", stack.dsn, "-q", "-v", "ON_ERROR_STOP=1"],
        DOCKER_TIMEOUT_SECONDS,
        stdin_text=sql,
    )
    _start_dashboard(stack, image, network)


def _start_postgres(stack: QaStack, password: str, network: str, volume: str) -> None:
    _docker_checked(
        [
            "run", "-d", "--name", stack.host, "--network", network,
            "-v", f"{volume}:/var/lib/postgresql/data",
            "-p", f"127.0.0.1:{_free_port()}:{QA_PORT}",
            "-e", f"POSTGRES_USER={QA_USER}",
            "-e", f"POSTGRES_PASSWORD={password}",
            "-e", f"POSTGRES_DB={stack.database}",
            QA_POSTGRES_IMAGE,
        ],
        DOCKER_TIMEOUT_SECONDS,
    )
    stack.containers.append(stack.host)
    # Probe over TCP: during first-time init the image runs a socket-only server.
    ready_probe = [
        "exec", stack.host, "pg_isready", "-h", "127.0.0.1", "-U", QA_USER, "-d", stack.database,
    ]
    _wait_until(lambda: _probe(ready_probe), "postgres")


def _start_dashboard(stack: QaStack, image: str, network: str) -> None:
    dashboard = f"{QA_PREFIX}{stack.run_id}-dashboard"
    host_port = _free_port()
    _docker_checked(
        [
            "run", "-d", "--name", dashboard, "--network", network,
            "-p", f"127.0.0.1:{host_port}:{DASHBOARD_CONTAINER_PORT}",
            "-e", f"DASHBOARD_BOARD_PG_DSN={stack.dsn}",
            image, "ahawr-dashboard", "--host", "0.0.0.0",
        ],
        DOCKER_TIMEOUT_SECONDS,
    )
    stack.containers.append(dashboard)
    _wait_until(lambda: _dashboard_healthy(host_port), "dashboard")
    stack.dashboard_url = f"http://127.0.0.1:{host_port}"


def _inprocess_up(fixture: str) -> QaStack:
    port = _free_port()
    process = _spawn_dashboard(port, FIXTURE_DIR / f"{fixture}.json")
    ready = False
    try:
        _wait_until(lambda: _dashboard_healthy(port), "dashboard")
        ready = True
    finally:
        if not ready:
            _kill_tree(process)
    return QaStack(
        run_id=secrets.token_hex(_RUN_ID_BYTES),
        host="",
        database="",
        dsn="",
        dashboard_url=f"http://127.0.0.1:{port}",
        process=process,
    )


def _spawn_dashboard(port: int, fixture_file: Path) -> subprocess.Popen[bytes]:
    search_path = [str(CLAUDE_RUNNER_DIR / "src"), os.environ.get("PYTHONPATH", "")]
    env = {
        name: value for name, value in os.environ.items() if name != DASHBOARD_DSN_ENV
    }
    env |= {
        DASHBOARD_FIXTURE_ENV: str(fixture_file),
        "PYTHONPATH": os.pathsep.join(entry for entry in search_path if entry),
    }
    return subprocess.Popen(
        [
            sys.executable, "-c",
            "import sys; from claude_runner.dashboard import main; sys.exit(main(sys.argv[1:]))",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        env=env,
        start_new_session=sys.platform != "win32",
    )


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, check=False, timeout=DOCKER_TIMEOUT_SECONDS,
            )
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=DOCKER_TIMEOUT_SECONDS)


def _remove_seeded_runs(run_id: str) -> None:
    """Remove the run dirs seeded for this stack, leaving other qa-* runs."""
    prefix = f"{QA_PREFIX}{run_id}-"
    if not QA_RUNS_DIR.is_dir():
        return
    for path in QA_RUNS_DIR.glob(prefix + "*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            log.info("removed seeded qa run dir %s", path.name)


def _seed_verdict_files(run_id: str, fixture: str) -> None:
    """Write the fixture's seeded verdicts into one run dir per verdict, each
    as verdict.json (the exact name board_api._qa_matches globs:
    ``qa-*/verdict.json``)."""
    fixture_path = FIXTURE_DIR / f"{fixture}.json"
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("qa fixture %s unreadable: %s", fixture_path.name, type(exc).__name__)
        return
    verdicts = data.get("qa_verdicts")
    if not isinstance(verdicts, list):
        return
    for index, verdict in enumerate(verdicts):
        if not isinstance(verdict, dict):
            log.warning(
                "qa_verdicts entry %d is not an object in %s: %s",
                index, fixture_path.name, type(verdict).__name__,
            )
            continue
        run_dir = QA_RUNS_DIR / f"{QA_PREFIX}{run_id}-{index}"
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")


def _seed_sql(fixture: str) -> str:
    fixture_path = FIXTURE_DIR / f"{fixture}.json"
    tables: dict[str, list[dict[str, object]]] = json.loads(
        fixture_path.read_text(encoding="utf-8")
    )["tables"]
    statements = _n8n_table_statements()
    for table, rows in tables.items():
        for row in rows:
            if table.startswith(N8N_TABLE_PREFIX):
                name = _identifier(table, MIXED_CASE)
                statements.append(_insert_statement(f'public."{name}"', row, quoted=True))
            else:
                statements.append(_insert_statement(f"board.{_identifier(table)}", row))
    return "\n".join(statements)


def _n8n_table_statements() -> list[str]:
    """CREATE TABLE of the four n8n data tables (public schema, mixed-case names) with the column
    layout of the real tables, so a throwaway dashboard finds the tables it reads."""
    layout: dict[str, list[list[str]]] = json.loads(N8N_TABLES_PATH.read_text(encoding="utf-8"))
    statements = []
    for table, columns in layout.items():
        definitions = []
        for column, data_type in columns:
            name = _identifier(column, MIXED_CASE)
            if column == "id":
                definitions.append('"id" integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY')
            elif column in ("createdAt", "updatedAt"):
                definitions.append(f'"{name}" timestamptz NOT NULL DEFAULT now()')
            else:
                definitions.append(f'"{name}" {_column_type(data_type)}')
        table_name = _identifier(table, MIXED_CASE)
        columns_sql = ", ".join(definitions)
        statements.append(f'CREATE TABLE IF NOT EXISTS public."{table_name}" ({columns_sql});')
    return statements


def _column_type(data_type: str) -> str:
    if data_type not in N8N_COLUMN_TYPES:
        raise ValueError(f"column type {data_type!r} is not one of {', '.join(N8N_COLUMN_TYPES)}")
    return data_type


def _insert_statement(table: str, row: dict[str, object], quoted: bool = False) -> str:
    if quoted:
        columns = ", ".join(f'"{_identifier(column, MIXED_CASE)}"' for column in row)
    else:
        columns = ", ".join(_identifier(column) for column in row)
    values = ", ".join(_sql_literal(value) for value in row.values())
    return f"INSERT INTO {table} ({columns}) VALUES ({values});"


def _identifier(name: str, pattern: re.Pattern[str] = _IDENTIFIER_PATTERN) -> str:
    if not pattern.fullmatch(name):
        raise ValueError(f"{name!r} is not a plain SQL identifier")
    return name


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise ValueError(f"fixture value {value!r} has no SQL literal")


def _teardown_commands(stack: QaStack) -> list[list[str]]:
    commands = [["rm", "-f", name] for name in stack.containers]
    commands += [["volume", "rm", name] for name in stack.volumes]
    commands += [["network", "rm", name] for name in stack.networks]
    commands += [["rmi", "-f", name] for name in stack.images]
    for command in commands:
        _assert_qa_name(command[-1])
    return commands


def _assert_qa_name(name: str) -> None:
    if not name.startswith(QA_PREFIX):
        raise QaTargetError(f"object {name!r} does not carry the {QA_PREFIX!r} prefix")


def _wait_until(ready: Callable[[], bool], what: str) -> None:
    for _poll in range(HEALTH_MAX_POLLS):
        if ready():
            return
        time.sleep(HEALTH_POLL_SECONDS)
    raise QaStackError(f"{what} is not healthy after {HEALTH_MAX_POLLS} polls")


def _probe(args: list[str]) -> bool:
    try:
        return _docker(args, DOCKER_TIMEOUT_SECONDS).returncode == 0
    except QaStackError:
        return False


def _dashboard_healthy(host_port: int) -> bool:
    url = f"http://127.0.0.1:{host_port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=HEALTH_REQUEST_SECONDS) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, OSError):
        return False


def _docker_checked(
    args: list[str], timeout_seconds: float, stdin_text: str | None = None
) -> None:
    result = _docker(args, timeout_seconds, stdin_text)
    if result.returncode != 0:
        detail = result.stderr.strip()[-ERROR_TEXT_MAX_CHARS:]
        raise QaStackError(f"docker {args[0]} failed with exit code {result.returncode}: {detail}")


def _present(name: str) -> bool:
    try:
        return _docker(["inspect", name], DOCKER_TIMEOUT_SECONDS).returncode == 0
    except QaStackError:
        return False


def _docker(
    args: list[str], timeout_seconds: float, stdin_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise QaStackError(f"docker {args[0]} did not finish in {timeout_seconds:g} s") from error
    except OSError as error:
        raise QaStackError(f"docker {args[0]} could not run: {error}") from error


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
