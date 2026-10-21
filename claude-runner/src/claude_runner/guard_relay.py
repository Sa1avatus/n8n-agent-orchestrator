"""Telegram long-poll relay: getUpdates to the n8n webhook.

The offset (``update_id + 1``) is saved only after the webhook answered 2xx.  Logs carry
update ids, HTTP status codes and exception class names, never the token, a URL or text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("claude_runner.guard_relay")
# httpx logs request URLs at INFO, and the bot token sits in the Telegram URL path.
logging.getLogger("httpx").setLevel(logging.WARNING)

TELEGRAM_API_URL = "https://api.telegram.org"
TOKEN_VAR = "DASHBOARD_TELEGRAM_BOT_TOKEN"
WEBHOOK_URL_VAR = "DASHBOARD_GUARDS_TG_WEBHOOK_URL"
OFFSET_PATH_VAR = "DASHBOARD_GUARDS_TG_OFFSET_PATH"
DRY_RUN_VAR = "DASHBOARD_GUARDS_DRY_RUN"
POLL_TIMEOUT_S = 50
POLL_LIMIT = 50
ALLOWED_UPDATES = ["message", "callback_query"]
HTTP_MARGIN_S = 20.0
WEBHOOK_TIMEOUT_S = 30.0
BACKOFF_STEP_S = 3.0
BACKOFF_MAX_S = 60.0
# Copy of (name, description) from data-tables/ops/tg_commands.py; the relay test checks equality.
BOT_COMMANDS: list[tuple[str, str]] = [
    ("status", "Состояние текущей миссии: задача, попытка, оценка, агенты (также /progress)"),
    ("agents", "Что сейчас делают Worker и Reviewer (также /tasks)"),
    ("queue", "Очередь миссий: list, add, rm, move, clear"),
    ("start", "Запустить миссию: /start id (без аргумента - приветствие)"),
    ("resume", "Продолжить или перезапустить миссию: /resume id (также /restart)"),
    ("stop", "Остановить миссию после текущего шага"),
    ("pause", "Пауза: /pause причина, снять - /pause off"),
    ("retry", "Разрешить ещё одну попытку текущей задачи"),
    ("accept", "Принять текущую задачу как выполненную"),
    ("steer", "Добавить указание агентам: /steer текст"),
    ("approve", "Подтвердить ожидающее изменение: /approve код"),
    ("deny", "Отклонить ожидающее изменение"),
    ("undo", "Отменить последнее подтверждённое изменение настроек"),
    ("missions", "Список миссий: id, название, включена ли"),
    ("mission", "Включить или выключить миссию: /mission id enable"),
    ("audit", "Журнал команд бота: /audit N"),
    ("config", "Какие настройки можно менять"),
    ("set", "Изменить настройку: /set ключ значение"),
    ("model", "Модель роли: /model role model"),
    ("usage", "Где смотреть токены, скорость и кэш"),
    ("insights", "Оценки и повторы по задачам"),
    ("errors", "Последние ошибки выполнения"),
    ("profile", "Профиль и разрешённые чаты"),
    ("version", "Версии workflow, runner и n8n"),
    ("whoami", "Ваш уровень доступа и номер чата"),
    ("help", "Список команд с описанием: /help фильтр (также /commands)"),
]


class RelayError(Exception):
    """A Telegram or webhook answer the relay cannot use; the message holds no secrets."""


@dataclass(frozen=True)
class RelaySettings:
    webhook_url: str
    offset_path: str
    dry_run: bool
    backoff_step_s: float = BACKOFF_STEP_S
    backoff_max_s: float = BACKOFF_MAX_S


def load_relay_settings(env: Mapping[str, str]) -> RelaySettings:
    missing = [name for name in (WEBHOOK_URL_VAR, OFFSET_PATH_VAR) if not env.get(name, "").strip()]
    if missing:
        raise ValueError(f"guard relay variables missing: {', '.join(missing)}")
    dry_run = env.get(DRY_RUN_VAR, "").strip().lower() in ("1", "true", "yes", "on")
    return RelaySettings(
        webhook_url=env[WEBHOOK_URL_VAR].strip(),
        offset_path=env[OFFSET_PATH_VAR].strip(),
        dry_run=dry_run,
    )


def build_telegram_client(env: Mapping[str, str]) -> httpx.AsyncClient:
    token = env.get(TOKEN_VAR, "").strip()
    if not token:
        raise ValueError(f"{TOKEN_VAR} is not set")
    return httpx.AsyncClient(
        base_url=f"{TELEGRAM_API_URL}/bot{token}/",
        timeout=POLL_TIMEOUT_S + HTTP_MARGIN_S,
    )


def build_webhook_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_S)


def backoff_seconds(fails: int, step_s: float, max_s: float) -> float:
    return min(max_s, step_s * fails)


def load_offset(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except FileNotFoundError:
        return 0
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"offset file {path} does not hold an integer") from exc


def save_offset(path: str, offset: int) -> None:
    staging_path = f"{path}.part"
    with open(staging_path, "w", encoding="utf-8") as handle:
        handle.write(f"{offset}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staging_path, path)


def _json_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError as exc:
        raise RelayError(f"getUpdates answer is not JSON (HTTP {resp.status_code})") from exc


async def _wait_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


class TelegramRelay:
    def __init__(
        self,
        settings: RelaySettings,
        telegram: httpx.AsyncClient,
        webhook: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.telegram = telegram
        self.webhook = webhook
        self.offset = 0
        self.commands_set = False

    async def run(self, stop: asyncio.Event) -> None:
        if self.settings.dry_run:
            logger.info(
                "dry-run: would long-poll getUpdates (timeout %d s); nothing polled", POLL_TIMEOUT_S
            )
            await stop.wait()
            return
        self.offset = load_offset(self.settings.offset_path)
        logger.info("relay started, offset %d", self.offset)
        fails = 0
        while not stop.is_set():
            try:
                cycle_task = asyncio.create_task(self._cycle())
                stop_task = asyncio.create_task(stop.wait())
                done, _pending = await asyncio.wait(
                    {cycle_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
                )
                stop_task.cancel()
                if cycle_task in done:
                    cycle_task.result()
                else:
                    cycle_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cycle_task
                fails = 0
            except (httpx.HTTPError, OSError, RelayError) as exc:
                fails += 1
                delay_s = backoff_seconds(
                    fails, self.settings.backoff_step_s, self.settings.backoff_max_s
                )
                logger.warning("error #%d: %s, retry in %.0f s", fails, _describe(exc), delay_s)
                await _wait_or_stop(stop, delay_s)
        logger.info("relay stopped, offset %d", self.offset)

    async def _cycle(self) -> None:
        if not self.commands_set:
            await self._set_commands()
            self.commands_set = True
        try:
            for update in await self._get_updates():
                await self._forward(update)
        except asyncio.CancelledError:
            # Cancelled mid-poll; the offset advances only after a 2xx, so nothing is lost.
            raise

    async def _set_commands(self) -> None:
        resp = await self.telegram.post(
            "setMyCommands",
            json={
                "commands": [
                    {"command": name, "description": text} for name, text in BOT_COMMANDS
                ]
            },
        )
        body = _json_body(resp)
        if resp.status_code != 200 or not (isinstance(body, dict) and body.get("ok") is True):
            raise RelayError(f"setMyCommands returned HTTP {resp.status_code}")

    async def _get_updates(self) -> list[Any]:
        resp = await self.telegram.post(
            "getUpdates",
            json={
                "offset": self.offset,
                "timeout": POLL_TIMEOUT_S,
                "limit": POLL_LIMIT,
                "allowed_updates": ALLOWED_UPDATES,
            },
        )
        if resp.status_code != 200:
            raise RelayError(f"getUpdates returned HTTP {resp.status_code}")
        body = _json_body(resp)
        result: Any = None
        if isinstance(body, dict) and body.get("ok") is True:
            result = body.get("result")
        if not isinstance(result, list):
            raise RelayError("getUpdates answer has no ok result list")
        return result

    async def _forward(self, update: Any) -> None:
        update_id = update.get("update_id") if isinstance(update, dict) else None
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            raise RelayError("update without an integer update_id")
        resp = await self.webhook.post(self.settings.webhook_url, json=update)
        if not 200 <= resp.status_code < 300:
            raise RelayError(f"webhook returned HTTP {resp.status_code} for update {update_id}")
        self.offset = update_id + 1
        save_offset(self.settings.offset_path, self.offset)
        logger.info("forwarded update %d (HTTP %d)", update_id, resp.status_code)


def _describe(exc: Exception) -> str:
    return str(exc) if isinstance(exc, RelayError) else type(exc).__name__
