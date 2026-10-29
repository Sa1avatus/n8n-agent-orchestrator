"""Tests for the QA design contract: parsing, validation and loading."""

from __future__ import annotations

from pathlib import Path

from claude_runner.qa_contract import (
    CONTRACT_FILE_NAME,
    Item,
    NoContract,
    Screen,
    load_contract,
    parse_contract,
    validate_contract,
)

VALID = """
# Board: providers

## Providers page
- Open: route /providers, viewport 1280x800
- Layout: cards in a row, each with Connection, API key and Models; a Delete provider
  button in the header; a Fetch models line with a name filter and a Free only switch
- Reference image: providers-reference.png
- C1: every provider card shows its connection name and its key's first characters
- C2: the header has exactly one Delete provider button per card
- C3: the list shows no rows while there is no provider in the database
"""


def _write(mission_dir: Path, text: str, images: tuple[str, ...] = ()) -> Path:
    contract_dir = mission_dir / "mission"
    contract_dir.mkdir(parents=True)
    (contract_dir / CONTRACT_FILE_NAME).write_text(text, encoding="utf-8")
    for image in images:
        (contract_dir / image).write_bytes(b"png")
    return contract_dir


def test_valid_contract_parses_and_validates(tmp_path: Path) -> None:
    contract_dir = _write(tmp_path, VALID, images=("providers-reference.png",))
    contract = load_contract("mission", contracts_root=tmp_path)
    assert not isinstance(contract, NoContract)
    assert contract.title == "Board: providers"
    assert contract.screens == (
        Screen(
            name="Providers page",
            route_or_steps="route /providers",
            viewport="1280x800",
            layout=(
                "cards in a row, each with Connection, API key and Models; a Delete provider "
                "button in the header; a Fetch models line with a name filter and a Free only "
                "switch"
            ),
            reference_image="providers-reference.png",
        ),
    )
    assert contract.items == (
        Item("C1", "every provider card shows its connection name and its key's first characters"),
        Item("C2", "the header has exactly one Delete provider button per card"),
        Item("C3", "the list shows no rows while there is no provider in the database"),
    )
    assert validate_contract(contract, contract_dir) == []


def test_missing_title(tmp_path: Path) -> None:
    text = VALID.replace("# Board: providers\n\n", "")
    contract_dir = _write(tmp_path, text, images=("providers-reference.png",))
    contract = parse_contract(text)
    assert validate_contract(contract, contract_dir) == ["missing title"]


def test_screen_without_route(tmp_path: Path) -> None:
    text = VALID.replace("- Open: route /providers, viewport 1280x800\n", "- Viewport: 1280x800\n")
    contract_dir = _write(tmp_path, text, images=("providers-reference.png",))
    contract = parse_contract(text)
    assert validate_contract(contract, contract_dir) == [
        "screen 1 (Providers page) is missing route"
    ]


def test_screen_without_viewport(tmp_path: Path) -> None:
    text = VALID.replace(
        "- Open: route /providers, viewport 1280x800\n",
        "- Open: route /providers\n",
    )
    contract_dir = _write(tmp_path, text, images=("providers-reference.png",))
    contract = parse_contract(text)
    assert validate_contract(contract, contract_dir) == [
        "screen 1 (Providers page) is missing viewport"
    ]


def test_screen_without_reference(tmp_path: Path) -> None:
    text = VALID.replace("- Reference image: providers-reference.png\n", "")
    contract_dir = _write(tmp_path, text)
    contract = parse_contract(text)
    assert validate_contract(contract, contract_dir) == [
        "screen 1 (Providers page) is missing reference image"
    ]


def test_reference_image_missing(tmp_path: Path) -> None:
    contract_dir = _write(tmp_path, VALID)
    contract = parse_contract(VALID)
    assert validate_contract(contract, contract_dir) == [
        "screen 1 (Providers page): reference image providers-reference.png not found"
    ]


def test_reference_image_wrong_suffix(tmp_path: Path) -> None:
    text = VALID.replace("providers-reference.png", "providers-reference.txt")
    contract_dir = _write(tmp_path, text)
    (contract_dir / "providers-reference.txt").write_bytes(b"txt")
    contract = parse_contract(text)
    assert validate_contract(contract, contract_dir) == [
        "screen 1 (Providers page): reference image providers-reference.txt not found"
    ]


def test_items_not_sequential(tmp_path: Path) -> None:
    text = VALID.replace(
        "- C2: the header has exactly one Delete provider button per card\n",
        "- C4: gap item\n",
    )
    contract_dir = _write(tmp_path, text, images=("providers-reference.png",))
    contract = parse_contract(text)
    assert validate_contract(contract, contract_dir) == [
        "item ids must be C1..C3 without gaps or duplicates"
    ]


def test_item_ids_duplicated(tmp_path: Path) -> None:
    text = VALID.replace(
        "- C2: the header has exactly one Delete provider button per card\n",
        "- C1: duplicate\n",
    )
    contract_dir = _write(tmp_path, text, images=("providers-reference.png",))
    contract = parse_contract(text)
    assert validate_contract(contract, contract_dir) == [
        "item ids must be C1..C3 without gaps or duplicates"
    ]


def test_no_items(tmp_path: Path) -> None:
    text = VALID.replace(
        "- C1: every provider card shows its connection name and its key's first characters\n", ""
    ).replace(
        "- C2: the header has exactly one Delete provider button per card\n", ""
    ).replace("- C3: the list shows no rows while there is no provider in the database\n", "")
    contract_dir = _write(tmp_path, text, images=("providers-reference.png",))
    contract = parse_contract(text)
    assert validate_contract(contract, contract_dir) == ["no items"]


def test_missing_directory_gives_no_contract(tmp_path: Path) -> None:
    result = load_contract("ghost", contracts_root=tmp_path)
    assert isinstance(result, NoContract)
    assert "no contract" in str(result)


def test_directory_without_contract_md(tmp_path: Path) -> None:
    mission_dir = tmp_path / "mission"
    mission_dir.mkdir()
    result = load_contract("mission", contracts_root=tmp_path)
    assert isinstance(result, NoContract)
    assert "no contract" in str(result)
