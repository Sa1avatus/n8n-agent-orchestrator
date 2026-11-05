"""Event detectors for the QA smoke crawl: pure checks over events the crawl recorded.

Nothing here imports Playwright or does I/O; the crawl converts browser events into the plain
dataclasses below and gets findings back.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from .qa_runner import _check_header, _Recorder

ERROR = "error"
WARNING = "warning"
HTTP_ERROR_MIN = 400
NULL_ID_TOKENS = ("/null", "/undefined", "=null", "=undefined", "%5Bobject")
ASSET_TYPES = frozenset({"image", "stylesheet"})
BAD_TEXT_PATTERN = re.compile(r"\b(?:undefined|null|NaN)\b|\[object Object\]")
I18N_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


@dataclass(frozen=True)
class Where:
    url: str
    element: str
    step: int
    screenshot: str


@dataclass(frozen=True)
class Finding:
    detector: str
    severity: str
    url: str
    element: str
    step: int
    screenshot: str
    reason: str


@dataclass(frozen=True)
class ConsoleMessage:
    type: str
    text: str


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int


@dataclass(frozen=True)
class FailedRequest:
    url: str
    resource_type: str


@dataclass(frozen=True)
class DomElement:
    selector: str
    text: str
    visible: bool = True
    x_px: float = 0
    width_px: float = 100
    height_px: float = 20
    parent_visible: bool = True


def _finding(
    detector: str, where: Where, url: str, reason: str, severity: str = ERROR
) -> Finding:
    return Finding(detector, severity, url, where.element, where.step, where.screenshot, reason)


def detect_console_errors(messages: Sequence[ConsoleMessage], where: Where) -> list[Finding]:
    return [
        _finding("console-error", where, where.url, message.text)
        for message in messages
        if message.type == "error"
    ]


def detect_pageerrors(errors: Sequence[str], where: Where) -> list[Finding]:
    return [_finding("pageerror", where, where.url, error) for error in errors]


def detect_client_errors(
    responses: Sequence[HttpResponse], allowed: Collection[int], where: Where
) -> list[Finding]:
    return [
        _finding("client-error", where, response.url, f"HTTP {response.status} {response.url}")
        for response in responses
        if response.status >= HTTP_ERROR_MIN and response.status not in allowed
    ]


def detect_null_ids(requests: Sequence[HttpRequest], where: Where) -> list[Finding]:
    return [
        _finding("null-id", where, request.url, f"{request.method} {request.url} has a null id")
        for request in requests
        if any(token in request.url for token in NULL_ID_TOKENS)
    ]


def detect_missing_header(
    requests: Sequence[HttpRequest], base_url: str, where: Where
) -> list[Finding]:
    findings: list[Finding] = []
    for request in requests:
        recorder = _Recorder()
        _check_header(base_url, recorder, request)
        findings.extend(
            _finding("missing-header", where, request.url, reason)
            for reason in recorder.network_errors
        )
    return findings


def detect_failed_assets(failures: Sequence[FailedRequest], where: Where) -> list[Finding]:
    return [
        _finding(
            "asset-failed", where, failure.url, f"{failure.resource_type} failed: {failure.url}"
        )
        for failure in failures
        if failure.resource_type in ASSET_TYPES
    ]


def detect_bad_text(elements: Sequence[DomElement], where: Where) -> list[Finding]:
    return [
        _finding(
            "bad-text", where, where.url, f"visible text {element.text!r} in {element.selector}"
        )
        for element in elements
        if element.visible and BAD_TEXT_PATTERN.search(element.text)
    ]


def detect_hidden_messages(appeared: Sequence[DomElement], where: Where) -> list[Finding]:
    return [
        _finding(
            "error-visible",
            where,
            where.url,
            f"message {element.text!r} appeared only in hidden {element.selector}",
        )
        for element in appeared
        if not element.visible and element.text.strip()
    ]


def _overflow_reason(element: DomElement, viewport_width_px: float) -> str | None:
    if not element.visible:
        return None
    right_px = element.x_px + element.width_px
    if right_px > viewport_width_px:
        return (
            f"{element.selector} ends at {right_px:g}px, past the {viewport_width_px:g}px viewport"
        )
    if element.parent_visible and (element.width_px == 0 or element.height_px == 0):
        return f"zero-size {element.selector} inside a visible container"
    return None


def detect_overflow(
    elements: Sequence[DomElement], viewport_width_px: float, where: Where
) -> list[Finding]:
    findings: list[Finding] = []
    for element in elements:
        reason = _overflow_reason(element, viewport_width_px)
        if reason is not None:
            findings.append(_finding("overflow", where, where.url, reason))
    return findings


def _untranslated_keys(
    shown_lang: str,
    shown: Sequence[DomElement],
    other_lang: str,
    other: Sequence[DomElement],
    where: Where,
) -> list[Finding]:
    other_by_selector = {element.selector: element for element in other}
    findings: list[Finding] = []
    for element in shown:
        counterpart = other_by_selector.get(element.selector)
        if (
            counterpart is None
            or not element.visible
            or not counterpart.visible
            or not I18N_KEY_PATTERN.fullmatch(element.text.strip())
        ):
            continue
        translated = counterpart.text.strip()
        if translated and not I18N_KEY_PATTERN.fullmatch(translated):
            reason = (
                f"{shown_lang} shows i18n key {element.text.strip()!r} in {element.selector},"
                f" {other_lang} has {translated!r}"
            )
            findings.append(_finding("bad-text", where, where.url, reason))
    return findings


def detect_untranslated_keys(
    english: Sequence[DomElement], russian: Sequence[DomElement], where: Where
) -> list[Finding]:
    return _untranslated_keys("en", english, "ru", russian, where) + _untranslated_keys(
        "ru", russian, "en", english, where
    )


def detect_no_effect(
    before: Sequence[DomElement], after: Sequence[DomElement], disabled: bool, where: Where
) -> list[Finding]:
    if disabled or list(before) != list(after):
        return []
    return [
        _finding(
            "no-effect",
            where,
            where.url,
            f"click on {where.element} changed nothing on the page",
            severity=WARNING,
        )
    ]
