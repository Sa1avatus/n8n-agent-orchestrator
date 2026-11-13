"""One source of the settings defaults that more than one module needs.

The data is in ``settings_defaults.json`` (runner address, engine retry settings, retrieval limits).
The dashboard reads it through ``board_defaults.js``, which is generated from the JSON by
``python -m ahawr_runner.settings_defaults``; a test fails when the generated file is out of date.
Add a shared default to the JSON, not to a second module.
"""

from __future__ import annotations

import copy
import json
from functools import cache
from pathlib import Path
from typing import Any

DEFAULTS_PATH = Path(__file__).with_name("settings_defaults.json")
JS_PATH = Path(__file__).with_name("board_defaults.js")
JS_HEADER = (
    "// GENERATED from settings_defaults.json by `python -m ahawr_runner.settings_defaults`.\n"
    "// Do not edit: change the JSON and run the command again.\n"
)


@cache
def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    return data


def section(name: str) -> dict[str, Any]:
    """A copy of one section of the defaults, safe to change."""
    section_data: dict[str, Any] = copy.deepcopy(_load()[name])
    return section_data


def runner_url() -> str:
    return str(_load()["runner"]["url"])


def runner_port() -> int:
    return int(_load()["runner"]["port"])


def runner_loopback_url() -> str:
    return str(_load()["runner"]["loopback_url"])


def retrieval_url() -> str:
    return str(_load()["retrieval"]["url"])


def retrieval_defaults(corpora_name: str) -> dict[str, Any]:
    """The retrieval defaults under the setting names of the caller."""
    values = section("retrieval")
    return {
        "retrieval_enabled": values["enabled"],
        "retrieval_url": values["url"],
        corpora_name: values["corpora"],
        "retrieval_timeout_ms": values["timeout_ms"],
        "retrieval_worker_max_tokens": values["worker_max_tokens"],
        "retrieval_reviewer_max_tokens": values["reviewer_max_tokens"],
        "retrieval_label": values["label"],
    }


def render_js() -> str:
    body = json.dumps(_load(), indent=2, ensure_ascii=False)
    return (
        "(function () {\n"
        f"{JS_HEADER}"
        f"const DEFAULTS = {body};\n"
        'if (typeof module !== "undefined" && module.exports) {\n'
        "  module.exports = DEFAULTS;\n"
        '} else if (typeof document !== "undefined") {\n'
        "  globalThis.BoardDefaults = DEFAULTS;\n"
        "}\n"
        "})();\n"
    )


def main() -> int:
    JS_PATH.write_text(render_js(), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
