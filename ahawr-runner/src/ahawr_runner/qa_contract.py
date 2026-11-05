"""QA design contract: parse and validate a mission's ``contract.md``.

A contract is the agreed design of a mission's UI: one section per screen (the
route or the way to open it, the viewport, the layout and the reference image) and
the numbered check items C1..Cn. ``parse_contract`` reads it into a dataclass and
``validate_contract`` returns the format errors; ``load_contract`` finds the
contract for a mission and a missing one is a distinct ``NoContract`` result, not
an error.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import qa_stack

# The directory holding the per-mission contracts.
CONTRACTS_ENV = "QA_CONTRACTS_DIR"
CONTRACTS_ROOT = Path(
    os.environ.get(CONTRACTS_ENV) or qa_stack.CLAUDE_RUNNER_DIR / "qa" / "contracts"
)
CONTRACT_FILE_NAME = "contract.md"
# The reference images the agent compares screenshots against.
REFERENCE_IMAGE_SUFFIX = ".png"
TITLE_PATTERN = re.compile(r"^# (.+)$", re.MULTILINE)
SCREEN_PATTERN = re.compile(r"^## (.+)$", re.MULTILINE)
ITEM_PATTERN = re.compile(r"^- C([1-9][0-9]*): (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Screen:
    name: str
    route_or_steps: str
    viewport: str
    layout: str
    reference_image: str


@dataclass(frozen=True)
class Item:
    id: str
    text: str


@dataclass(frozen=True)
class Contract:
    title: str
    screens: tuple[Screen, ...]
    items: tuple[Item, ...]


class NoContract(Exception):
    """The mission has no contract: the directory is absent or holds no contract.md."""


def parse_contract(text: str) -> Contract:
    """Parse a contract.md into a Contract.

    Missing fields and item numbering are not parse errors: ``validate_contract``
    reports them as error strings, so a malformed text still parses into a Contract.
    """
    title_match = TITLE_PATTERN.search(text)
    title = title_match.group(1).strip() if title_match else ""
    screens = _parse_screens(text)
    items = _parse_items(text)
    return Contract(title=title, screens=screens, items=items)


def _parse_screens(text: str) -> tuple[Screen, ...]:
    headers = list(SCREEN_PATTERN.finditer(text))
    screens = []
    for index, match in enumerate(headers):
        start = match.end()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        fields = _fields(text[start:end])
        route, viewport = _route_and_viewport(fields.get("open", ""))
        screens.append(
            Screen(
                name=match.group(1).strip(),
                route_or_steps=route,
                viewport=viewport or fields.get("viewport", ""),
                layout=fields.get("layout", ""),
                reference_image=fields.get("reference image", ""),
            )
        )
    return tuple(screens)


def _route_and_viewport(open_value: str) -> tuple[str, str]:
    # The documented format writes the viewport in the Open line: "route /x, viewport 1280x800".
    parts = re.split(r"\bviewport\b", open_value, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip().rstrip(",").strip(), parts[1].strip()
    return open_value, ""


def _fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for block in re.split(r"(?m)^\s*- ", body)[1:]:
        key, _, value = block.partition(":")
        if not _:
            continue
        fields[key.strip().lower()] = " ".join(value.split())
    return fields


def _parse_items(text: str) -> tuple[Item, ...]:
    items = []
    for match in ITEM_PATTERN.finditer(text):
        items.append(Item(id=f"C{match.group(1)}", text=match.group(2).strip()))
    return tuple(items)


def validate_contract(contract: Contract, contract_dir: Path) -> list[str]:
    """Return the format errors of a contract; an empty list means it is valid.

    Errors: a missing title, a screen without its route, viewport, layout or
    reference image, a reference image that is not a png next to the contract and
    does not exist there, item ids not numbered C1..Cn in order, and no items.
    """
    errors: list[str] = []
    if not contract.title:
        errors.append("missing title")
    for index, screen in enumerate(contract.screens, start=1):
        for label, value in (
            ("route", screen.route_or_steps),
            ("viewport", screen.viewport),
            ("layout", screen.layout),
            ("reference image", screen.reference_image),
        ):
            if not value:
                errors.append(f"screen {index} ({screen.name}) is missing {label}")
        image_name = screen.reference_image
        if image_name and not _reference_exists(image_name, contract_dir):
            errors.append(
                f"screen {index} ({screen.name}): reference image {image_name} not found"
            )
    if not contract.items:
        errors.append("no items")
    elif not _items_sequential(contract.items):
        count = len(contract.items)
        errors.append(f"item ids must be C1..C{count} without gaps or duplicates")
    return errors


def _reference_exists(name: str, contract_dir: Path) -> bool:
    if not name.endswith(REFERENCE_IMAGE_SUFFIX):
        return False
    return (contract_dir / name).is_file()


def _items_sequential(items: tuple[Item, ...]) -> bool:
    return [int(item.id[1:]) for item in items] == list(range(1, len(items) + 1))


def load_contract(mission_id: str, contracts_root: Path = CONTRACTS_ROOT) -> Contract | NoContract:
    """Find and parse the mission's contract; a missing one is a NoContract result."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", mission_id):
        return NoContract(f"mission id {mission_id!r} is not a valid folder name")
    contract_path = contracts_root / mission_id / CONTRACT_FILE_NAME
    if not contract_path.is_file():
        return NoContract(f"no contract for {mission_id!r}: {contract_path} is missing")
    return parse_contract(contract_path.read_text(encoding="utf-8"))
