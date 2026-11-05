"""Tests for data-tables/ops/mission_lint.py: fixture missions and the PASS/FAIL exit code."""

import http.client
import importlib.util
import socket
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest

OPS_LINT = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "mission_lint.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mission_lint"


def _load_lint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mission_lint", OPS_LINT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint = _load_lint()


def _snapshot() -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in sorted(FIXTURES.rglob("*")) if path.is_file()}


PROVIDERS = FIXTURES / "providers_snapshot.json"


def _run(
    name: str, capsys: pytest.CaptureFixture[str], providers: Path | None = None
) -> tuple[int, str]:
    argv = [str(FIXTURES / name)]
    if providers is not None:
        argv += ["--providers", str(providers)]
    code = lint.main(argv)
    return code, capsys.readouterr().out


def test_all_pass_fixture_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-all-pass.csv", capsys)
    assert code == 0
    assert "FAIL" not in out
    for check in ("staged-input", "working-directory", "task-ids", "acceptance-criterion 1"):
        assert f"PASS fixture-all-pass {check}:" in out


def test_missing_staged_input_fails(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-missing-staged.csv", capsys)
    assert code == 1
    reason = "FAIL fixture-missing-staged staged-input: staged/missing_export.json does not exist"
    assert reason in out
    assert "PASS fixture-missing-staged working-directory:" in out


def test_missing_working_directory_fails(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-missing-workdir.csv", capsys)
    assert code == 1
    assert "FAIL fixture-missing-workdir working-directory: no_such_dir is not a directory" in out
    assert "PASS fixture-missing-workdir staged-inputs:" in out


def test_colliding_task_id_fails(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-colliding-id.csv", capsys)
    assert code == 1
    assert "FAIL fixture-colliding-id task-ids: T001A collide with the Architect split ids" in out


def test_empty_criterion_fails(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-empty-criterion.csv", capsys)
    assert code == 1
    assert "FAIL fixture-empty-criterion acceptance-criterion 1: the criterion is empty" in out


def test_criterion_without_check_fails(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-no-check.csv", capsys)
    assert code == 1
    assert "FAIL fixture-no-check acceptance-criterion 1:" in out
    assert "names no command or test path" in out


def test_unknown_mission_id_fails(capsys: pytest.CaptureFixture[str]) -> None:
    code = lint.main(["no-such-mission-id"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL no-such-mission-id: mission file" in out
    assert "missions/mission_no-such-mission-id.csv" in out


def test_roles_models_known(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-roles-known.csv", capsys, PROVIDERS)
    assert code == 0
    assert "PASS fixture-roles-known roles-models: all 3 model(s) in the providers list" in out


def test_roles_models_unknown_names_the_model(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-roles-unknown.csv", capsys, PROVIDERS)
    assert code == 1
    assert (
        "FAIL fixture-roles-unknown roles-models: model(s) not in the providers list: "
        "mistral/mistral-large-latest"
    ) in out
    assert "openrouter/deepseek/deepseek-chat" not in out.split("not in the providers list:")[1]


def test_roles_models_not_run_when_snapshot_missing(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(
        "mission_fixture-roles-known.csv", capsys, FIXTURES / "no_providers_snapshot.json"
    )
    assert code == 0
    assert "NOT RUN fixture-roles-known roles-models:" in out
    assert "does not exist" in out


def test_roles_models_not_run_when_snapshot_is_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run("mission_fixture-roles-known.csv", capsys, FIXTURES / "staged_export.json")
    assert code == 0
    assert "NOT RUN fixture-roles-known roles-models:" in out
    assert "has no providers list" in out


def test_roles_model_check_makes_no_network_call(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def _blocked(*args: object, **kwargs: object) -> None:
        calls.append(f"{args[0] if args else ''}")
        raise AssertionError("the lint opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    monkeypatch.setattr(http.client.HTTPConnection, "request", _blocked)
    code, out = _run("mission_fixture-roles-unknown.csv", capsys, PROVIDERS)
    assert code == 1
    assert not calls
    assert "roles-models" in out


def test_script_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    before = _snapshot()
    for name in sorted(path.name for path in FIXTURES.glob("mission_*.csv")):
        code = lint.main([str(FIXTURES / name)])
        assert code in (0, 1), f"unexpected exit code {code} for {name}"
    capsys.readouterr()
    assert _snapshot() == before
