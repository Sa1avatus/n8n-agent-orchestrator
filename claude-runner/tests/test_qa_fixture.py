from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_runner.board import InMemoryBoardStore
from claude_runner.qa_fixture import REQUIRED_TABLES, board_store_from_rows, load_fixture

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS.parent / "qa" / "fixtures"
CASES: dict[str, Any] = json.loads((TESTS / "qa_fixture_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(CASES["fixtures"]))
def test_fixture_counts_match_cases(name: str) -> None:
    rows = load_fixture(FIXTURES / f"{name}.json")
    counts = {
        "missions": len(rows.missions),
        "queue": len(rows.queue),
        "live_state": len(rows.live_state),
    }
    assert counts == CASES["fixtures"][name]


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read fixture"):
        load_fixture(tmp_path / "absent.json")


def test_bad_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_fixture(path)


def test_non_object_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a JSON object"):
        load_fixture(path)


@pytest.mark.parametrize("case", CASES["invalid"], ids=lambda c: c["name"])
def test_invalid_payload_raises(case: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=case["reason"]):
        load_fixture(case["payload"])


def test_board_store_from_rows_seeds_missions_queue_and_live_state() -> None:
    rows = load_fixture(FIXTURES / "default.json")
    store = board_store_from_rows(rows)
    assert isinstance(store, InMemoryBoardStore)
    assert len(store.list_missions()) == len(rows.missions)
    assert [entry.mission_id for entry in store.queue()] == [
        row["mission_id"] for row in sorted(rows.queue, key=lambda r: int(r["position"]))
    ]
    assert len(store.list_live_states()) == len(rows.live_state)
    mission = store.get_mission("m01")
    assert mission is not None and mission.enabled is True and mission.version == 3.0


def test_default_fixture_seeds_a_provider_with_models_and_no_secret_in_the_view() -> None:
    store = board_store_from_rows(load_fixture(FIXTURES / "default.json"))
    provider = store.get_provider("qa-cloud")
    assert provider is not None
    assert [m["model"] for m in provider["models"]] == ["qa-fast", "qa-think"]
    assert "api_key" not in provider


def test_providers_must_be_a_list_of_objects() -> None:
    payload = {"tables": {t: [] for t in REQUIRED_TABLES}, "providers": ["x"]}
    with pytest.raises(ValueError, match="providers"):
        load_fixture(payload)
