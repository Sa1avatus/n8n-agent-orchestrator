from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from claude_runner import guard_relay
from claude_runner.guard_relay import (
    BACKOFF_MAX_S,
    BACKOFF_STEP_S,
    BOT_COMMANDS,
    RelaySettings,
    TelegramRelay,
)

TOKEN = "123456:TEST-TOKEN"
WEBHOOK_URL = "https://n8n.test/webhook/guard-tg"
SECRET_TEXT = "secret order text"
OPS_COMMANDS_PATH = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "tg_commands.py"


class FakeTelegram:
    def __init__(self, updates: list[dict[str, Any]]) -> None:
        self.updates = updates
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.stop: asyncio.Event | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body: dict[str, Any] = json.loads(request.content)
        self.calls.append((method, body))
        if method == "setMyCommands":
            return httpx.Response(200, json={"ok": True, "result": True})
        pending = [update for update in self.updates if update["update_id"] >= body["offset"]]
        if not pending and self.stop is not None:
            self.stop.set()
        return httpx.Response(200, json={"ok": True, "result": pending})

    def offsets(self) -> list[int]:
        return [body["offset"] for method, body in self.calls if method == "getUpdates"]


class FakeWebhook:
    def __init__(self, statuses: list[int] | None = None) -> None:
        self.statuses = list(statuses or [])
        self.received: list[dict[str, Any]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.received.append(json.loads(request.content))
        return httpx.Response(self.statuses.pop(0) if self.statuses else 200)


def _update(update_id: int, text: str = SECRET_TEXT) -> dict[str, Any]:
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": 42}}}


def _relay(
    tmp_path: Path,
    telegram: FakeTelegram,
    webhook: FakeWebhook,
    dry_run: bool = False,
    backoff_step_s: float = BACKOFF_STEP_S,
    backoff_max_s: float = BACKOFF_MAX_S,
) -> TelegramRelay:
    settings = RelaySettings(
        webhook_url=WEBHOOK_URL,
        offset_path=str(tmp_path / "offset.txt"),
        dry_run=dry_run,
        backoff_step_s=backoff_step_s,
        backoff_max_s=backoff_max_s,
    )
    return TelegramRelay(
        settings,
        httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{TOKEN}/",
            transport=httpx.MockTransport(telegram.handle),
        ),
        httpx.AsyncClient(transport=httpx.MockTransport(webhook.handle)),
    )


async def _serve(relay: TelegramRelay, telegram: FakeTelegram) -> None:
    stop = asyncio.Event()
    telegram.stop = stop
    await asyncio.wait_for(relay.run(stop), timeout=10)


def _record_waits(
    monkeypatch: pytest.MonkeyPatch, delays: list[float], stop_after: int
) -> None:
    async def fake_wait(stop: asyncio.Event, seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= stop_after:
            stop.set()

    monkeypatch.setattr(guard_relay, "_wait_or_stop", fake_wait)


def _ops_commands() -> list[tuple[str, str]]:
    tree = ast.parse(OPS_COMMANDS_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "COMMANDS" for target in node.targets
        ):
            return [(name, text) for name, _category, text in ast.literal_eval(node.value)]
    raise AssertionError("COMMANDS not found in tg_commands.py")


def test_command_list_equals_ops_file() -> None:
    if not OPS_COMMANDS_PATH.exists():
        pytest.skip("data-tables/ops is not in git; the command list cannot be compared here")
    assert _ops_commands() == BOT_COMMANDS


def test_set_commands_sent_once_before_polling(tmp_path: Path) -> None:
    telegram = FakeTelegram([])
    asyncio.run(_serve(_relay(tmp_path, telegram, FakeWebhook()), telegram))
    set_calls = [body for method, body in telegram.calls if method == "setMyCommands"]
    assert set_calls == [{"commands": [{"command": n, "description": d} for n, d in BOT_COMMANDS]}]
    assert [method for method, _body in telegram.calls][:2] == ["setMyCommands", "getUpdates"]


def test_offset_saved_after_webhook_accepts(tmp_path: Path) -> None:
    telegram = FakeTelegram([_update(5), _update(6)])
    webhook = FakeWebhook()
    asyncio.run(_serve(_relay(tmp_path, telegram, webhook), telegram))
    assert (tmp_path / "offset.txt").read_text(encoding="utf-8") == "7\n"
    assert [item["update_id"] for item in webhook.received] == [5, 6]
    assert telegram.offsets() == [0, 7]


def test_offset_not_advanced_when_webhook_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    telegram = FakeTelegram([_update(5)])
    webhook = FakeWebhook(statuses=[500, 500])
    delays: list[float] = []
    _record_waits(monkeypatch, delays, stop_after=2)
    asyncio.run(_serve(_relay(tmp_path, telegram, webhook), telegram))
    assert not (tmp_path / "offset.txt").exists()
    assert telegram.offsets() == [0, 0]
    assert [item["update_id"] for item in webhook.received] == [5, 5]


def test_backoff_grows_and_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = FakeTelegram([_update(5)])
    webhook = FakeWebhook(statuses=[500, 500, 500])
    delays: list[float] = []
    _record_waits(monkeypatch, delays, stop_after=99)
    relay = _relay(tmp_path, telegram, webhook, backoff_step_s=3.0, backoff_max_s=7.0)
    asyncio.run(_serve(relay, telegram))
    assert delays == [3.0, 6.0, 7.0]
    assert (tmp_path / "offset.txt").read_text(encoding="utf-8") == "6\n"


def test_offset_survives_restart(tmp_path: Path) -> None:
    telegram = FakeTelegram([_update(5)])
    webhook = FakeWebhook()
    asyncio.run(_serve(_relay(tmp_path, telegram, webhook), telegram))
    assert (tmp_path / "offset.txt").read_text(encoding="utf-8") == "6\n"

    telegram.updates.append(_update(9))
    telegram.calls.clear()
    asyncio.run(_serve(_relay(tmp_path, telegram, webhook), telegram))
    assert telegram.offsets()[0] == 6
    assert [item["update_id"] for item in webhook.received] == [5, 9]


def test_dry_run_makes_no_call(tmp_path: Path) -> None:
    telegram = FakeTelegram([_update(5)])
    webhook = FakeWebhook()
    relay = _relay(tmp_path, telegram, webhook, dry_run=True)

    async def run_briefly() -> None:
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.05, stop.set)
        await relay.run(stop)

    asyncio.run(run_briefly())
    assert telegram.calls == []
    assert webhook.received == []
    assert not (tmp_path / "offset.txt").exists()


def test_logs_hold_no_token_or_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    telegram = FakeTelegram([_update(5)])
    webhook = FakeWebhook(statuses=[500])
    _record_waits(monkeypatch, [], stop_after=99)
    caplog.set_level(logging.DEBUG)
    asyncio.run(_serve(_relay(tmp_path, telegram, webhook), telegram))
    assert "forwarded update 5" in caplog.text
    assert TOKEN not in caplog.text
    assert SECRET_TEXT not in caplog.text
