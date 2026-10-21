from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from claude_runner import qa_stack
from claude_runner.qa_stack import (
    HEALTH_MAX_POLLS,
    QaStack,
    QaStackError,
    QaTargetError,
    assert_qa_target,
    build_qa_dsn,
    new_qa_password,
    stack_down,
    stack_up,
)


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.stdin: list[str | None] = []
        self.events: list[str] = []
        self.sleeps: list[float] = []
        self.fail_when: Callable[[list[str]], bool] | None = None

    def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv[0] == "docker"
        assert kwargs["timeout"] > 0
        args = argv[1:]
        self.calls.append(args)
        self.stdin.append(kwargs.get("input"))
        self.events.append("docker")
        failed = self.fail_when is not None and self.fail_when(args)
        return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "boom" if failed else "")

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(qa_stack.subprocess, "run", fake.run)
    monkeypatch.setattr(qa_stack.time, "sleep", fake.sleep)
    monkeypatch.setattr(qa_stack, "_dashboard_healthy", lambda _port: True)
    return fake


def _created_names(calls: list[list[str]]) -> list[str]:
    names: list[str] = []
    for call in calls:
        if call[:2] in (["network", "create"], ["volume", "create"]):
            names.append(call[2])
        if call[0] == "exec":
            names.append(call[1])
        for flag in ("-t", "--name", "--network"):
            if flag in call:
                names.append(call[call.index(flag) + 1])
        if "-v" in call and ":" in call[call.index("-v") + 1]:
            names.append(call[call.index("-v") + 1].split(":")[0])
    return names


def test_stack_up_runs_build_postgres_schema_then_dashboard(docker: FakeDocker) -> None:
    stack_up()
    assert [call[0] for call in docker.calls] == [
        "build", "network", "volume", "run", "exec", "run", "run",
    ]
    assert "--rm" in docker.calls[5]
    assert "ahawr-dashboard" in docker.calls[6]


def test_every_created_name_carries_qa_prefix(docker: FakeDocker) -> None:
    stack = stack_up()
    names = _created_names(docker.calls)
    assert len(names) >= 6
    assert all(name.startswith("qa-") for name in names)
    assert stack.host.startswith("qa-")
    assert stack.database.startswith("qa_")


def test_stack_up_schema_and_seed_go_through_psql_stdin(docker: FakeDocker) -> None:
    stack_up("default")
    psql_index = next(index for index, call in enumerate(docker.calls) if "psql" in call)
    sql = docker.stdin[psql_index] or ""
    assert "CREATE SCHEMA IF NOT EXISTS board" in sql
    assert 'CREATE TABLE IF NOT EXISTS public."data_table_user_Yn9lOhVhQ9gFymNs"' in sql
    assert 'INSERT INTO public."data_table_user_hYAqO52ZGHXxhhHX"' in sql


def test_empty_fixture_applies_schema_without_seed_rows(docker: FakeDocker) -> None:
    stack_up("empty")
    psql_index = next(index for index, call in enumerate(docker.calls) if "psql" in call)
    sql = docker.stdin[psql_index] or ""
    assert "CREATE SCHEMA IF NOT EXISTS board" in sql
    assert "INSERT INTO" not in sql
    assert sql.count("CREATE TABLE IF NOT EXISTS public.") == 4


def test_stack_up_refuses_unknown_fixture(docker: FakeDocker) -> None:
    with pytest.raises(ValueError, match="not one of"):
        stack_up("production")
    assert docker.calls == []


def test_guard_runs_before_any_docker_call(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_guard = qa_stack.assert_qa_target

    def guard(host: str, database: str) -> None:
        docker.events.append("guard")
        real_guard(host, database)

    monkeypatch.setattr(qa_stack, "assert_qa_target", guard)
    stack_up()
    assert docker.events[0] == "guard"
    assert docker.events.count("guard") == 1


def test_refused_guard_stops_before_any_connection(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(host: str, database: str, password: str) -> str:
        raise QaTargetError("refused in test")

    monkeypatch.setattr(qa_stack, "build_qa_dsn", refuse)
    with pytest.raises(QaTargetError, match="refused in test"):
        stack_up()
    assert docker.calls == []


def test_stack_down_removes_only_the_listed_qa_objects(docker: FakeDocker) -> None:
    stack = QaStack(
        run_id="abc",
        host="qa-abc",
        database="qa_abc",
        dsn="postgresql://qa:x@qa-abc:5432/qa_abc",
        containers=["qa-abc", "qa-abc-dashboard"],
        volumes=["qa-abc-data"],
        networks=["qa-abc-net"],
    )
    stack_down(stack)
    assert docker.calls == [
        ["rm", "-f", "qa-abc"],
        ["rm", "-f", "qa-abc-dashboard"],
        ["volume", "rm", "qa-abc-data"],
        ["network", "rm", "qa-abc-net"],
    ]


def test_stack_down_refuses_unprefixed_object_before_any_removal(docker: FakeDocker) -> None:
    stack = QaStack(
        run_id="abc",
        host="qa-abc",
        database="qa_abc",
        dsn="postgresql://qa:x@qa-abc:5432/qa_abc",
        containers=["qa-abc"],
        volumes=["postgres_data"],
    )
    with pytest.raises(QaTargetError, match="does not carry"):
        stack_down(stack)
    assert docker.calls == []


def test_stack_down_after_stack_up_touches_only_qa_objects(docker: FakeDocker) -> None:
    stack = stack_up()
    calls_before_down = len(docker.calls)
    stack_down(stack)
    removals = docker.calls[calls_before_down:]
    assert len(removals) == 5
    assert all(call[-1].startswith("qa-") for call in removals)


def test_unhealthy_postgres_is_bounded_then_cleaned_up(docker: FakeDocker) -> None:
    docker.fail_when = lambda args: args[0] == "exec" and "pg_isready" in args
    with pytest.raises(QaStackError, match="postgres is not healthy"):
        stack_up()
    assert len(docker.sleeps) == HEALTH_MAX_POLLS
    assert [call[0] for call in docker.calls[-4:]] == ["rm", "volume", "network", "rmi"]
    assert docker.calls[-4][-1].startswith("qa-")


def test_failed_container_removal_still_removes_volume_and_network(docker: FakeDocker) -> None:
    stack = QaStack(
        run_id="abc",
        host="qa-abc",
        database="qa_abc",
        dsn="postgresql://qa:x@qa-abc:5432/qa_abc",
        containers=["qa-abc", "qa-abc-dashboard"],
        volumes=["qa-abc-data"],
        networks=["qa-abc-net"],
    )
    docker.fail_when = lambda args: args == ["rm", "-f", "qa-abc"]
    with pytest.raises(QaStackError, match="teardown of QA stack abc incomplete"):
        stack_down(stack)
    assert ["volume", "rm", "qa-abc-data"] in docker.calls
    assert ["network", "rm", "qa-abc-net"] in docker.calls
    assert ["rm", "-f", "qa-abc-dashboard"] in docker.calls


def test_cleanup_failure_does_not_mask_unhealthy_dashboard(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(qa_stack, "_dashboard_healthy", lambda _port: False)
    docker.fail_when = lambda args: args[0] == "rm"
    with pytest.raises(QaStackError, match="dashboard is not healthy"):
        stack_up()
    assert "cleanup of failed QA stack" in caplog.text


def test_stack_down_removes_image_with_qa_name(docker: FakeDocker) -> None:
    stack = QaStack(
        run_id="abc",
        host="qa-abc",
        database="qa_abc",
        dsn="postgresql://qa:x@qa-abc:5432/qa_abc",
        images=["qa-dashboard:abc"],
    )
    stack_down(stack)
    assert docker.calls == [["rmi", "-f", "qa-dashboard:abc"]]


def test_unhealthy_dashboard_raises_qa_stack_error(
    docker: FakeDocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa_stack, "_dashboard_healthy", lambda _port: False)
    with pytest.raises(QaStackError, match="dashboard is not healthy"):
        stack_up()
    assert len(docker.sleeps) == HEALTH_MAX_POLLS


def test_docker_timeout_raises_qa_stack_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(argv: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(qa_stack.subprocess, "run", hang)
    with pytest.raises(QaStackError, match="did not finish"):
        stack_up()


def test_stack_repr_hides_dsn() -> None:
    stack = QaStack(run_id="abc", host="qa-abc", database="qa_abc", dsn="secret-dsn")
    assert "secret-dsn" not in repr(stack)


def test_sql_literal_escapes_quotes() -> None:
    assert qa_stack._sql_literal("O'Brien") == "'O''Brien'"


@pytest.mark.parametrize(
    ("host", "database"),
    [
        ("qa-run-1", "qa_run_1"),
        ("qa-7f3a", "qa_scratch"),
    ],
)
def test_accepts_throwaway_host_and_database(host: str, database: str) -> None:
    assert_qa_target(host, database)


@pytest.mark.parametrize(
    ("host", "database", "reason"),
    [
        ("postgres", "qa_run_1", "production name"),
        ("n8n", "qa_run_1", "production name"),
        ("localhost", "qa_run_1", "production name"),
        ("127.0.0.1", "qa_run_1", "production name"),
        ("prod-db", "qa_run_1", "does not carry the 'qa-' prefix"),
        ("qa-run-1", "postgres", "production database"),
        ("qa-run-1", "ahawr", "production database"),
        ("qa-run-1", "ahawr_live", "does not carry the 'qa_' prefix"),
        ("qa-run-1", "n8n_copy", "does not carry the 'qa_' prefix"),
    ],
)
def test_refuses_production_or_unprefixed_target(host: str, database: str, reason: str) -> None:
    with pytest.raises(QaTargetError, match=reason):
        assert_qa_target(host, database)


def test_refuses_invalid_qa_names() -> None:
    with pytest.raises(QaTargetError, match="not a valid qa- host name"):
        assert_qa_target("qa-Run_1", "qa_run_1")
    with pytest.raises(QaTargetError, match="not a valid qa_ database name"):
        assert_qa_target("qa-run-1", "qa_run-1")


def test_build_qa_dsn_from_generated_parts() -> None:
    dsn = build_qa_dsn("qa-run-1", "qa_run_1", "abc_DEF-123")
    assert dsn == "postgresql://qa:abc_DEF-123@qa-run-1:5432/qa_run_1"


def test_build_qa_dsn_refuses_production_target() -> None:
    with pytest.raises(QaTargetError, match="production name"):
        build_qa_dsn("postgres", "qa_run_1", "abc123")


@pytest.mark.parametrize("password", ["a@b", "pw/x", "p:w", "with space", ""])
def test_build_qa_dsn_refuses_password_that_breaks_the_url(password: str) -> None:
    with pytest.raises(QaTargetError, match="URL-safe"):
        build_qa_dsn("qa-run-1", "qa_run_1", password)


def test_new_qa_password_is_fresh_on_each_call() -> None:
    first = new_qa_password()
    second = new_qa_password()
    assert first != second
    assert len(first) >= 32
