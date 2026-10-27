from __future__ import annotations

from types import SimpleNamespace

import pytest

from claude_runner import qa_stack
from claude_runner.qa_stack import QaStackError, stack_down, stack_up

QA_HOST = "qa-postgres"
QA_PASSWORD = "secret-pass"
FAKE_PORT = 5556


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    sql_runs: list[tuple[str, str]] = []
    spawned: list[tuple[int, object, str | None]] = []
    killed: list[object] = []
    process = SimpleNamespace(name="dashboard")

    monkeypatch.setenv(qa_stack.QA_PG_HOST_ENV, QA_HOST)
    monkeypatch.setenv(qa_stack.QA_PG_PASSWORD_ENV, QA_PASSWORD)
    monkeypatch.setattr(qa_stack, "_run_sql", lambda dsn, sql: sql_runs.append((dsn, sql)))
    monkeypatch.setattr(qa_stack, "_free_port", lambda: FAKE_PORT)
    monkeypatch.setattr(qa_stack, "_dashboard_healthy", lambda _port: True)
    monkeypatch.setattr(qa_stack, "_seed_verdict_files", lambda *_args: None)
    monkeypatch.setattr(qa_stack, "_remove_seeded_runs", lambda _run_id: None)
    monkeypatch.setattr(qa_stack, "_kill_tree", killed.append)

    def spawn(port: int, fixture_file: object, dsn: str | None = None) -> SimpleNamespace:
        spawned.append((port, fixture_file, dsn))
        return process

    monkeypatch.setattr(qa_stack, "_spawn_dashboard", spawn)
    return SimpleNamespace(sql_runs=sql_runs, spawned=spawned, killed=killed, process=process)


def test_postgres_stack_creates_a_database_seeds_it_and_starts_the_dashboard(
    fakes: SimpleNamespace,
) -> None:
    stack = stack_up("default", mode="postgres")

    assert stack.database == f"qa_{stack.run_id}"
    assert stack.dashboard_url == f"http://127.0.0.1:{FAKE_PORT}"
    admin_dsn, create_sql = fakes.sql_runs[0]
    assert f"/{qa_stack.QA_ADMIN_DATABASE}" in admin_dsn
    assert create_sql == f'CREATE DATABASE "{stack.database}"'
    seed_dsn, seed_sql = fakes.sql_runs[1]
    assert seed_dsn == stack.dsn
    assert "CREATE SCHEMA" in seed_sql.upper()
    assert fakes.spawned == [(FAKE_PORT, None, stack.dsn)]


def test_postgres_stack_down_kills_the_dashboard_and_drops_the_database(
    fakes: SimpleNamespace,
) -> None:
    stack = stack_up("default", mode="postgres")
    fakes.sql_runs.clear()

    stack_down(stack)

    assert fakes.killed == [fakes.process]
    assert [sql for _dsn, sql in fakes.sql_runs] == [
        f'DROP DATABASE IF EXISTS "{stack.database}" WITH (FORCE)'
    ]


def test_postgres_stack_needs_the_server_settings(
    fakes: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(qa_stack.QA_PG_PASSWORD_ENV)

    with pytest.raises(QaStackError, match="needs QA_PG_HOST and QA_PG_PASSWORD"):
        stack_up("default", mode="postgres")

    assert fakes.sql_runs == []


def test_postgres_stack_refuses_a_production_host(
    fakes: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(qa_stack.QA_PG_HOST_ENV, "postgres")

    with pytest.raises(qa_stack.QaTargetError):
        stack_up("default", mode="postgres")

    assert fakes.sql_runs == []


def test_failed_start_drops_the_database_again(
    fakes: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa_stack, "_dashboard_healthy", lambda _port: False)
    monkeypatch.setattr(qa_stack, "HEALTH_MAX_POLLS", 1)
    monkeypatch.setattr(qa_stack.time, "sleep", lambda _seconds: None)

    with pytest.raises(QaStackError):
        stack_up("default", mode="postgres")

    assert fakes.killed == [fakes.process]
    assert fakes.sql_runs[-1][1].startswith("DROP DATABASE IF EXISTS")
