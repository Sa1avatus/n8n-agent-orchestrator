"""Action catalogue and argument validation for board ops commands.

Dashboard-side mirror of the agent whitelist (``docs/board-ops.md``, "Action
whitelist"): ``ACTIONS`` names every whitelisted action with its argument
schema (allowed values / ranges), the dangerous flag, the task-boundary
requirement, whether the action is safe to repeat, and the per-argument text
for the UI — the ``consequences`` string in Russian for the confirmation
dialog and the ``command_preview`` display string.

``validate_action(name, args)`` rejects an unknown action, an unknown or
missing argument, a value outside its allowed set (services, presets) or
range (memory 2-8 GB), a pair id that is not a key of the model-pairs
setting, and any value carrying a path separator or a shell metacharacter.
It returns the rejection reasons; an empty list means the command is
admissible.
"""

from __future__ import annotations

from typing import Any, Callable

from . import board_ops_settings

__all__ = [
    "ACTIONS",
    "MEMORY_GB",
    "MEMORY_SERVICES",
    "PRESETS",
    "REBUILD_SERVICES",
    "validate_action",
]

# Whitelisted services and their allowed argument values (docs/board-ops.md,
# "Action whitelist").  All argv is built from these fixed dictionaries; the
# request only supplies dictionary keys, never a path or a container name.
REBUILD_SERVICES: tuple[str, ...] = (
    "claude-runner",
    "ahawr-dashboard",
    "ahawr-retrieval",
    "ahawr-litellm",
)

MEMORY_SERVICES: tuple[str, ...] = ("claude-runner", "ahawr-retrieval")

# docker_update_memory: the allowed GB values (2-8 GB).
MEMORY_GB: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 8)

# set_llama_preset: the existing scripts, run unmodified.
PRESETS: tuple[str, ...] = ("enable_thinking", "ensure_prod_preset")

# No argument may carry a path separator.
_PATH_SEPARATORS = ("/", "\\")

# No argument may carry a shell metacharacter either (separators,
# redirections, quoting, globs, expansion, comment).
_SHELL_METACHARACTERS = set(";&|<>(){}[]$'\"`*?~#")


def _no_args_consequences(text: str) -> Callable[[dict[str, Any]], str]:
    """The fixed Russian text for an action that takes no arguments."""

    def consequences(_args: dict[str, Any]) -> str:
        return text

    return consequences


def _rebuild_service_consequences(args: dict[str, Any]) -> str:
    service = args.get("service", "<service>")
    return (
        f"Пересборка сервиса {service} (docker compose up -d --build); "
        f"после — проверка здоровья сервиса должна пройти. Выполняется только "
        f"на границе задания или когда миссия не запущена."
    )


def _docker_update_memory_consequences(args: dict[str, Any]) -> str:
    service = args.get("service", "<service>")
    gb = args.get("gb", 0)
    return (
        f"Запись для контейнера {service}: "
        f"--memory={gb}g --memory-swap={gb}g."
    )


def _set_llama_preset_consequences(args: dict[str, Any]) -> str:
    preset = args.get("preset", "<preset>")
    return (
        f"Запуск сценария {preset}.sh без изменений; ожидание до 450 с, "
        f"пока llama-server станет здоров."
    )


def _switch_model_pair_consequences(args: dict[str, Any]) -> str:
    pair = args.get("pair", "<pair>")
    return (
        f"Обновление hermes_config (модели и провайдеры worker/reviewer) на пару "
        f"«{pair}» с оптимистичной блокировкой; вступает в силу со "
        f"следующего запуска."
    )


ACTIONS: dict[str, dict[str, Any]] = {
    "stop_mission": {
        "args": {},
        "dangerous": True,
        "requires_boundary": False,
        "repeatable": False,
        "consequences": _no_args_consequences(
            "Остановка текущего прогона Worker через API runner и убийство цепочки "
            "очереди; состояние миссии не трогается — возобновление продолжит с "
            "текущего задания."
        ),
        "command_preview": lambda _args: "cancel the running run, kill the queue chain",
    },
    "pause_at_boundary": {
        "args": {},
        "dangerous": True,
        "requires_boundary": True,
        "repeatable": False,
        "consequences": _no_args_consequences(
            "Ожидание смены id задания у Worker (дедлайн 8 ч), затем остановка "
            "цепочки очереди и контейнера n8n."
        ),
        "command_preview": lambda _args: (
            "wait for the task boundary (8 h deadline), stop the queue chain "
            "and the n8n container"
        ),
    },
    "resume": {
        "args": {},
        "dangerous": False,
        "requires_boundary": False,
        "repeatable": True,
        "consequences": _no_args_consequences(
            "Запуск queue_watch, который сам запускает очередь."
        ),
        "command_preview": lambda _args: "start queue_watch (which starts the queue)",
    },
    "restart_queue": {
        "args": {},
        "dangerous": True,
        "requires_boundary": False,
        "repeatable": False,
        "consequences": _no_args_consequences(
            "Остановка цепочки очереди и перезапуск real_queue.sh в отрыве."
        ),
        "command_preview": lambda _args: "kill the queue chain, relaunch real_queue.sh",
    },
    "restart_queue_watch": {
        "args": {},
        "dangerous": True,
        "requires_boundary": False,
        "repeatable": False,
        "consequences": _no_args_consequences(
            "Остановка queue_watch и его перезапуск."
        ),
        "command_preview": lambda _args: "kill queue_watch, relaunch it",
    },
    "restart_docker_watchdog": {
        "args": {},
        "dangerous": True,
        "requires_boundary": False,
        "repeatable": False,
        "consequences": _no_args_consequences(
            "Перезапуск docker_watchdog.ps1."
        ),
        "command_preview": lambda _args: "relaunch docker_watchdog.ps1",
    },
    "restart_tg_relay": {
        "args": {},
        "dangerous": True,
        "requires_boundary": False,
        "repeatable": False,
        "consequences": _no_args_consequences(
            "Перезапуск tg_relay (выполняется, только если релея не запущен и "
            "существует маркер tg_relay.enabled; иначе команда отклоняется)."
        ),
        "command_preview": lambda _args: "relaunch tg_relay",
    },
    "rebuild_service": {
        "args": {"service": {"allowed": REBUILD_SERVICES}},
        "dangerous": True,
        "requires_boundary": True,
        "repeatable": False,
        "consequences": _rebuild_service_consequences,
        "command_preview": lambda args: (
            f"docker compose up -d --build {args.get('service', '<service>')} + health probe"
        ),
    },
    "docker_update_memory": {
        "args": {"service": {"allowed": MEMORY_SERVICES}, "gb": {"allowed": MEMORY_GB}},
        "dangerous": True,
        "requires_boundary": False,
        "repeatable": False,
        "consequences": _docker_update_memory_consequences,
        "command_preview": lambda args: (
            f"docker update --memory={args.get('gb', 0)}g --memory-swap={args.get('gb', 0)}g "
            f"{args.get('service', '<service>')}"
        ),
    },
    "set_llama_preset": {
        "args": {"preset": {"allowed": PRESETS}},
        "dangerous": True,
        "requires_boundary": True,
        "repeatable": False,
        "consequences": _set_llama_preset_consequences,
        "command_preview": lambda args: (
            f"bash {args.get('preset', '<preset>')}.sh, wait up to 450 s for a healthy llama-server"
        ),
    },
    "switch_model_pair": {
        "args": {"pair": {"source": board_ops_settings.REGISTRY["verified_model_pairs"]}},
        "dangerous": True,
        "requires_boundary": False,
        "repeatable": False,
        "consequences": _switch_model_pair_consequences,
        "command_preview": lambda args: (
            f"update hermes_config to pair {args.get('pair', '<pair>')} "
            f"(worker/reviewer model+provider), effective from the next run"
        ),
    },
    "send_test_notification": {
        "args": {},
        "dangerous": False,
        "requires_boundary": False,
        "repeatable": True,
        "consequences": _no_args_consequences(
            "Отправка одного тестового сообщения по хостовому каналу уведомлений."
        ),
        "command_preview": lambda _args: "send one test message through the host path",
    },
}


def _check_value(
    action: str,
    arg: str,
    value: Any,
    spec: dict[str, Any],
    pairs: list[dict[str, Any]] | None,
) -> list[str]:
    """The errors of one argument value against its allowed set or range."""
    label = f"action {action!r} argument {arg!r}"
    if pairs is None:
        pairs = list(board_ops_settings.defaults().get("verified_model_pairs") or [])

    if "allowed" in spec:
        allowed = tuple(spec["allowed"])
        if not isinstance(value, type(allowed[0])):
            kind = type(allowed[0]).__name__
            return [f"{label}: expected a {kind}, got {type(value).__name__}"]
        if value not in allowed:
            if isinstance(value, str):
                value_text = repr(value)
            else:
                value_text = str(value)
            return [
                f"{label}: {value_text} is not an allowed value "
                f"(allowed: {', '.join(str(v) for v in allowed)})"
            ]
    elif spec.get("source") is board_ops_settings.REGISTRY["verified_model_pairs"]:
        if not isinstance(value, str) or not value:
            return [f"{label}: expected a pair id string, got {type(value).__name__}"]
        registered = [p.get("id") for p in pairs if isinstance(p, dict)]
        if value not in registered:
            if registered:
                known = ", ".join(repr(p) for p in registered)
            else:
                known = "none"
            return [
                f"{label}: {value!r} is not a key of the model pairs setting "
                f"(registered ids: {known})"
            ]
    else:
        raise AssertionError(f"unknown argument spec for {action!r}/{arg!r}")

    # Path separators and shell metacharacters are rejected for every
    # string argument, regardless of its allowed set.
    if isinstance(value, str):
        if any(separator in value for separator in _PATH_SEPARATORS):
            return [f"{label}: {value!r} contains a path separator"]
        if any(char in _SHELL_METACHARACTERS for char in value):
            return [f"{label}: {value!r} contains a shell metacharacter"]
    return []


def validate_action(
    name: str,
    args: dict[str, Any] | None,
    pairs: list[dict[str, Any]] | None = None,
) -> list[str]:
    """The rejection reasons for ``action``/``args``; empty when admissible.

    Rejects: an unknown action; args that are not a dict; unknown or missing
    arguments; a value outside its allowed set (services, presets) or range
    (memory 2-8 GB); a pair id that is not a key of the model-pairs setting;
    any string value carrying a path separator or a shell metacharacter.
    ``pairs`` is the current ``verified_model_pairs`` setting value; when
    omitted, the setting default is used.
    """
    if args is None:
        args = {}
    action = ACTIONS.get(name)
    if action is None:
        known = ", ".join(sorted(ACTIONS))
        return [f"unknown action {name!r} (known actions: {known})"]
    if not isinstance(args, dict):
        return [f"action {name!r}: args must be a dict, got {type(args).__name__}"]

    schema: dict[str, dict[str, Any]] = action["args"]
    errors: list[str] = []

    unknown = sorted(set(args) - set(schema))
    if unknown:
        allowed = ", ".join(sorted(schema)) or "none"
        errors.append(
            f"action {name!r}: unknown argument(s) "
            f"{', '.join(represent(u) for u in unknown)} (allowed: {allowed})"
        )
    missing = sorted(set(schema) - set(args))
    if missing:
        errors.append(
            f"action {name!r}: missing argument(s) {', '.join(represent(m) for m in missing)}"
        )
    for arg, value in args.items():
        if arg in schema:
            errors.extend(_check_value(name, arg, value, schema[arg], pairs))
    return errors


def represent(value: Any) -> str:
    """The quoted form of an argument name for error messages."""
    return repr(value) if isinstance(value, str) else str(value)
