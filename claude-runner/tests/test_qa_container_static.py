from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

QA_DIR = Path(__file__).resolve().parent.parent / "qa"
DOCKERFILE = QA_DIR / "Dockerfile.qa"
PATCH = QA_DIR / "compose-qa.patch"
QA_SERVICE = "ahawr-qa"
HEARTBEAT_TARGET = "/d/n8n/data-tables/ops/heartbeat"
RW_TARGETS = {"/qa-runs", HEARTBEAT_TARGET}
SHORT_VOLUME = re.compile(r"^(?P<source>.+):(?P<target>/[^:]*)(?::(?P<mode>ro|rw))?$")


def _dockerfile_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _dockerfile_instructions() -> list[str]:
    return [line for line in _dockerfile_text().splitlines() if line and not line.startswith("#")]


def _patch_hunks() -> list[list[str]]:
    hunks: list[list[str]] = []
    for line in PATCH.read_text(encoding="utf-8").splitlines():
        if line.startswith("@@"):
            hunks.append([])
        elif hunks and line[:1] in ("+", "-", " ") and not line.startswith(("+++", "---")):
            hunks[-1].append(line)
    return hunks


def _qa_service() -> dict[str, Any]:
    for hunk in _patch_hunks():
        added = [line[1:] for line in hunk if line.startswith("+")]
        if f"  {QA_SERVICE}:" in added:
            document = yaml.safe_load("services:\n" + "\n".join(added))
            service: dict[str, Any] = document["services"][QA_SERVICE]
            return service
    raise AssertionError(f"the patch adds no {QA_SERVICE} service")


def _qa_mounts() -> list[tuple[str, bool, bool]]:
    mounts: list[tuple[str, bool, bool]] = []
    for volume in _qa_service()["volumes"]:
        if isinstance(volume, dict):
            is_bind = volume["type"] == "bind"
            mounts.append((volume["target"], bool(volume.get("read_only")), is_bind))
            continue
        match = SHORT_VOLUME.match(volume)
        assert match, f"unparsed volume in {QA_SERVICE}: {volume}"
        source = match["source"]
        is_bind = any(char in source for char in "/\\$")
        mounts.append((match["target"], match["mode"] == "ro", is_bind))
    return mounts


def test_dockerfile_installs_no_docker_cli() -> None:
    offending = [line for line in _dockerfile_instructions() if "docker" in line.lower()]
    assert not offending, f"Dockerfile.qa installs or mentions Docker: {offending}"


def test_dockerfile_mounts_no_docker_socket() -> None:
    assert "docker.sock" not in _dockerfile_text(), "Dockerfile.qa refers to docker.sock"


def test_dockerfile_last_user_is_not_root() -> None:
    users = re.findall(r"(?m)^USER\s+(\S+)", _dockerfile_text())
    assert users, "Dockerfile.qa sets no USER"
    assert users[-1] not in {"root", "0"}, f"last USER of Dockerfile.qa is root: {users[-1]}"


def test_qa_service_is_not_privileged() -> None:
    assert _qa_service().get("privileged") is not True, f"{QA_SERVICE} runs privileged"


def test_qa_service_adds_no_capabilities() -> None:
    assert "cap_add" not in _qa_service(), f"{QA_SERVICE} adds Linux capabilities"


def test_qa_service_root_filesystem_is_read_only() -> None:
    assert _qa_service().get("read_only") is True, f"{QA_SERVICE} root filesystem is not read_only"


def test_qa_service_repository_and_mission_binds_are_read_only() -> None:
    binds = [
        (target, read_only)
        for target, read_only, is_bind in _qa_mounts()
        if is_bind and target != HEARTBEAT_TARGET
    ]
    assert binds, f"{QA_SERVICE} has no repository or mission bind to check"
    writable = [target for target, read_only in binds if not read_only]
    assert not writable, f"{QA_SERVICE} repository or mission binds are not :ro: {writable}"


def test_qa_service_only_rw_mounts_are_qa_runs_and_heartbeat() -> None:
    writable = {target for target, read_only, _ in _qa_mounts() if not read_only}
    assert writable == RW_TARGETS, f"rw mounts of {QA_SERVICE} are {sorted(writable)}"


def test_patch_removes_loopback_port_of_claude_runner() -> None:
    hunks = _patch_hunks()
    removed = [line for hunk in hunks for line in hunk if line.startswith("-")]
    added = [line for hunk in hunks for line in hunk if line.startswith("+")]
    assert "-    ports:" in removed, "the patch does not remove the ports key of claude-runner"
    assert any("127.0.0.1:8700:8700" in line for line in removed), (
        "the patch does not remove the 127.0.0.1:8700 ports line"
    )
    assert not any("127.0.0.1:8700" in line for line in added), (
        "the patch still publishes 127.0.0.1:8700"
    )
