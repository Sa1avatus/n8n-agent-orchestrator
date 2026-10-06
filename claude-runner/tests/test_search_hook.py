from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from claude_runner import config as config_module
from claude_runner.claude_cli import build_command
from claude_runner.config import ProviderProfile, Settings
from claude_runner.search_cli import main as search_main
from claude_runner.search_hook import (
    FIRST_MESSAGE,
    calls_search,
    decide,
    is_exploratory,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "apply.sh").write_text("echo hi\n")
    return tmp_path


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rg retry", True),
        ("rg -n retry src/", True),
        ("find . -name '*.py'", True),
        ("cd /x && find src -type f | head", True),
        ("git grep -n retry", True),
        ("grep -rn retry .", True),
        ("grep -R retry src", True),
        ("grep -nr retry src", True),
        ("grep -n retry src", True),  # src is a directory
        ("grep -n retry src/*.py", True),  # a glob
        ("grep --recursive retry src", True),
        ("grep -n 'def foo' scripts/apply.sh", False),  # checks a known file
        ("grep -c retry scripts/apply.sh scripts/apply.sh", False),
        ("grep -e retry -e delay scripts/apply.sh", False),
        ("cat scripts/apply.sh | grep retry", False),  # filters a pipe
        ("ls scripts && sed -n 1,20p scripts/apply.sh", False),
        ("python3 -m pytest -q", False),
        ("ahawr-search 'retry delay'", False),
    ],
)
def test_exploratory_commands(project: Path, command: str, expected: bool) -> None:
    assert is_exploratory(command, str(project)) is expected


def test_calls_search_ignores_command_v() -> None:
    assert calls_search('ahawr-search "retry delay" --k 8')
    assert calls_search("cd /x && ahawr-search retry | head -20")
    assert calls_search("command -v ahawr-search && ahawr-search retry")
    assert not calls_search("command -v ahawr-search")
    assert not calls_search("echo ahawr-search")


def call(session: str, tool: str, command: str = "", cwd: str = ".") -> dict[str, object]:
    return {
        "session_id": session,
        "tool_name": tool,
        "cwd": cwd,
        "tool_input": {"command": command} if command else {"pattern": "x"},
    }


def test_first_exploration_is_refused_until_ahawr_search_is_used(
    tmp_path: Path, project: Path
) -> None:
    state = tmp_path / "state"
    grep = call("s1", "Bash", "grep -rn retry .", str(project))
    assert decide(grep, state) == FIRST_MESSAGE
    assert "ahawr-search" in FIRST_MESSAGE and "--k 8" in FIRST_MESSAGE
    # a check of a known file is never refused
    assert decide(call("s1", "Bash", "grep -n x scripts/apply.sh", str(project)), state) is None
    # the Grep and Glob tools explore too
    assert decide(call("s1", "Grep"), state) == FIRST_MESSAGE
    # two refusals are the limit: a model that insists is let through
    assert decide(grep, state) is None
    # a session that searched is not refused
    assert decide(call("s2", "Bash", "ahawr-search 'retry'"), state) is None
    assert decide(call("s2", "Bash", "rg retry"), state) is None
    assert decide(call("s2", "Glob"), state) is None
    # sessions are independent
    assert decide(call("s3", "Bash", "rg retry"), state) == FIRST_MESSAGE


def test_reminder_every_few_explorations_after_a_search(tmp_path: Path) -> None:
    state = tmp_path / "state"
    assert decide(call("s", "Bash", "ahawr-search retry"), state, every=3) is None
    grep = call("s", "Bash", "rg retry")
    assert [decide(grep, state, every=3) for _ in range(3)] == [None, None, None]
    reminder = decide(grep, state, every=3)
    assert reminder is not None and "4 times" in reminder
    assert [decide(grep, state, every=3) for _ in range(3)] == [None, None, None]
    assert decide(grep, state, every=3) is not None
    # a new ahawr-search call restarts the count
    assert decide(call("s", "Bash", "ahawr-search more"), state, every=3) is None
    assert [decide(grep, state, every=3) for _ in range(3)] == [None, None, None]
    # every=0 turns the reminders off
    for _ in range(20):
        assert decide(grep, state, every=0) is None


def run_hook(config_dir: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    env = {"CLAUDE_CONFIG_DIR": str(config_dir), "PATH": "/usr/bin:/bin", "PYTHONPATH": "src"}
    return subprocess.run(
        [sys.executable, "-m", "claude_runner.search_hook"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_hook_process_blocks_with_exit_code_2_and_allows_otherwise(tmp_path: Path) -> None:
    blocked = run_hook(tmp_path, call("p1", "Bash", "rg retry"))
    assert blocked.returncode == 2
    assert "ahawr-search" in blocked.stderr
    assert run_hook(tmp_path, call("p1", "Bash", "ahawr-search retry")).returncode == 0
    assert run_hook(tmp_path, call("p1", "Bash", "rg retry")).returncode == 0
    # garbage input must never break a run
    garbage = subprocess.run(
        [sys.executable, "-m", "claude_runner.search_hook"],
        input="not json",
        capture_output=True,
        text=True,
        env={"CLAUDE_CONFIG_DIR": str(tmp_path), "PYTHONPATH": "src"},
        check=False,
    )
    assert garbage.returncode == 0
    log = (tmp_path / "search-first" / "decisions.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(line)["event"] for line in log] == ["deny-first", "search"]


def test_search_first_setting_needs_provider_role_and_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_module.shutil, "which", lambda name: "/bin/ahawr-search")
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__SEARCH_FIRST": "true",
        }
    )
    local = s.provider("local")
    assert local is not None and local.search_first is True
    assert s.search_first_for("worker", local)
    assert not s.search_first_for("reviewer", local)  # only the listed roles
    assert not s.search_first_for("worker", None)  # global default is off
    monkeypatch.setattr(config_module.shutil, "which", lambda name: None)
    assert not s.search_first_for("worker", local)  # the tool is not installed
    monkeypatch.setattr(config_module.shutil, "which", lambda name: "/bin/ahawr-search")
    everywhere = Settings.from_env(
        {"CLAUDE_RUNNER_SEARCH_FIRST": "1", "CLAUDE_RUNNER_SEARCH_FIRST_ROLES": "worker,generic"}
    )
    assert everywhere.search_first_for("generic", None)
    off = ProviderProfile(name="X", search_first=False)
    assert not everywhere.search_first_for("worker", off)  # a provider can opt out
    with pytest.raises(ValueError, match="SEARCH_FIRST"):
        Settings.from_env({"CLAUDE_RUNNER_PROVIDER_X__SEARCH_FIRST": "maybe"})


def test_command_carries_the_hook_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    s = Settings.from_env({"CLAUDE_RUNNER_SEARCH_FIRST_EVERY": "4"})
    plain = build_command(s, s.profile("worker"), model="m", claude_session_id="id", resume=False)
    assert "--settings" not in plain  # off: nothing changes

    cmd = build_command(
        s, s.profile("worker"), model="m", claude_session_id="id", resume=False, search_first=True
    )
    path = Path(cmd[cmd.index("--settings") + 1])
    assert path == tmp_path / "search-first-settings.json"
    hooks = json.loads(path.read_text("utf-8"))["hooks"]
    pre = hooks["PreToolUse"][0]
    assert pre["matcher"] == "Bash|Grep|Glob"
    command = pre["hooks"][0]["command"]
    assert "AHAWR_SEARCH_FIRST_EVERY=4" in command and "claude_runner.search_hook" in command
    assert "PreCompact" not in hooks  # no compact instructions for a provider without isolation

    # the local provider keeps its PreCompact hook in the same file
    local = ProviderProfile(name="LOCAL", env={"ANTHROPIC_AUTH_TOKEN": "t"})
    both = build_command(
        s,
        s.profile("worker"),
        model="m",
        claude_session_id="id",
        resume=False,
        provider=local,
        search_first=True,
    )
    merged = json.loads(Path(both[both.index("--settings") + 1]).read_text("utf-8"))["hooks"]
    assert set(merged) == {"PreToolUse", "PreCompact"}

    # the runner's own /compact run never gets the search hook
    compact = build_command(
        s,
        s.profile("worker"),
        model="",
        claude_session_id="id",
        resume=True,
        compact=True,
        provider=local,
        search_first=True,
    )
    assert Path(compact[compact.index("--settings") + 1]).name == "compact-settings.json"


def test_search_cli_accepts_the_spellings_a_model_guesses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, int, int]] = []

    async def fake_fetch(
        url: str, query: str, budget: int, *rest: object, **kw: object
    ) -> tuple[dict, str]:
        seen.append((query, budget, 0))
        return {"context": {"chunks": []}}, ""

    import claude_runner.search_cli as mod

    monkeypatch.setattr(mod, "fetch_detailed", fake_fetch)
    monkeypatch.setattr(mod, "resolve_root", lambda root: (root, None))
    assert search_main(["--query", "retry delay", "--k", "3"]) == 0
    assert search_main(["retry", "delay", "applied", "--limit", "2", "--max-tokens", "900"]) == 0
    assert search_main(["-q", "a b", "--no-such-flag", "1"]) == 0
    assert [(q, b) for q, b, _ in seen] == [
        ("retry delay", mod.DEFAULT_BUDGET),
        ("retry delay applied", 900),
        ("a b", mod.DEFAULT_BUDGET),
    ]
    assert "ignoring unknown option" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        search_main([])  # no query at all is still an error
