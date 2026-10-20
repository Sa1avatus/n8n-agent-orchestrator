"""Pure mapping from a local thinking policy to the env vars / CLI args of one cloud attempt.

The local policy (``think_policy.py`` / ``think_switch.py``) chooses a variant by task number
and, per attempt, the mode of its first window: ``first[0]`` for a first attempt, ``retry[0]``
for a retry.  For a cloud (Anthropic) run that mode is fixed for the whole CLI process
(``docs/cloud-thinking.md`` section 4), so it is encoded in the process environment:
mode ``off`` sets ``MAX_THINKING_TOKENS=0`` (``CLAUDE_CODE_DISABLE_THINKING`` is the
alternative), mode ``think`` leaves the variable unset.  No command-line flag switches
thinking, so ``args`` stays empty.

The module is pure: it parses the policy text with the same helpers the local path uses
and returns ``None`` whenever the setting is disabled, the provider is local, the policy
is empty / invalid, or the provider alias is unknown.  It performs no process, file,
or network I/O and names no model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from . import think_policy, think_switch

# The exact env var names this mapping may set in the child process env.  The runner
# keeps these out of its own ``CLAUDE_RUNNER_*`` whitelist (``child_env`` in
# ``claude_cli.py``) and applies them per attempt for cloud runs.
CLOUD_THINK_ENV_VARS: tuple[str, ...] = (
    "MAX_THINKING_TOKENS",
    "CLAUDE_CODE_DISABLE_THINKING",
)


@dataclass(frozen=True)
class CloudThinkSetting:
    """One cloud attempt's thinking mode: the env vars to set, the CLI args to add,
    the chosen variant name, and the mode (``think`` / ``nothink``)."""

    env: dict[str, str]
    args: list[str]
    variant: str
    mode: Literal["think", "nothink"]


def resolve_cloud_think(
    policy_text: str,
    task_number: int,
    is_retry: bool,
    provider: str,
    enabled: bool,
) -> CloudThinkSetting | None:
    """The cloud mode for one attempt under ``policy_text``.

    Returns ``None`` (no change to the process) when ``enabled`` is false, the
    provider is ``local``, the policy is empty / invalid, or the provider alias is
    unknown.  Otherwise returns the mode chosen exactly as the local path would
    choose it for the same policy, task number, and retry flag.
    """
    if not enabled:
        return None
    if not provider or provider.lower() == "local":
        return None
    # Unknown provider alias: only the runner's cloud provider is known to the mapping.
    if provider.lower() != "anthropic":
        return None
    text = (policy_text or "").strip().lower()
    if not text:
        return None

    variant_name: str
    if text.startswith("{"):
        policy, _ = think_policy.parse(text)
        if policy is None:
            return None
        variant = policy.variant_for(task_number)
        variant_name = variant.name
        mode = variant.retry[0][0] if is_retry else variant.first[0][0]
    else:
        parts = [part.strip() for part in text.split("|") if part.strip()]
        if not parts:
            return None
        variant_name = parts[(max(task_number, 1) - 1) % len(parts)]
        if variant_name == "off":
            mode = "off"
        elif variant_name == "off_first":
            mode = "off" if not is_retry else "think"
        elif (pulse := think_switch.parse_pulse(variant_name)) is not None:
            start = think_switch.start_think(variant_name, is_retry)
            # The local switch's first call of the attempt (``steps == 0``): it thinks
            # when the start window (``f``/``r``) is non-zero, else by the pulse cycle.
            mode = "think" if (start > 0 or think_switch.pulse_thinking(-start, *pulse)) else "off"
        else:
            return None
    if mode not in ("think", "off"):
        return None

    env: dict[str, str] = {}
    if mode == "off":
        env["MAX_THINKING_TOKENS"] = "0"
        env["CLAUDE_CODE_DISABLE_THINKING"] = "1"
    # mode "think": the variable stays unset (the CLI's default for the chosen model).
    return CloudThinkSetting(
        env=env,
        args=[],
        variant=variant_name,
        mode="think" if mode == "think" else "nothink",
    )
