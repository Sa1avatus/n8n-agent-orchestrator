from __future__ import annotations

from claude_runner.qa_smoke_detect import (
    WARNING,
    ConsoleMessage,
    DomElement,
    FailedRequest,
    HttpRequest,
    HttpResponse,
    Where,
    detect_bad_text,
    detect_client_errors,
    detect_console_errors,
    detect_failed_assets,
    detect_hidden_messages,
    detect_missing_header,
    detect_no_effect,
    detect_null_ids,
    detect_overflow,
    detect_pageerrors,
    detect_untranslated_keys,
)

BASE = "http://127.0.0.1:8080"
WHERE = Where(
    url=f"{BASE}/static/board.html#board/kanban",
    element="Save",
    step=2,
    screenshot="qa-1/step-2.png",
)
DASHBOARD = {"x-ahawr-dashboard": "1"}


def test_console_error_positive_carries_where() -> None:
    findings = detect_console_errors([ConsoleMessage("error", "boom")], WHERE)

    assert len(findings) == 1
    finding = findings[0]
    assert (finding.detector, finding.severity, finding.reason) == (
        "console-error",
        "error",
        "boom",
    )
    assert (finding.url, finding.element, finding.step) == (WHERE.url, "Save", 2)
    assert finding.screenshot == "qa-1/step-2.png"


def test_console_error_negative_ignores_warnings() -> None:
    assert detect_console_errors([ConsoleMessage("warning", "slow")], WHERE) == []


def test_pageerror_positive() -> None:
    findings = detect_pageerrors(["TypeError: x is undefined"], WHERE)

    assert [(f.detector, f.reason) for f in findings] == [
        ("pageerror", "TypeError: x is undefined")
    ]


def test_pageerror_negative() -> None:
    assert detect_pageerrors([], WHERE) == []


def test_client_error_positive_reports_unallowed_status() -> None:
    responses = [HttpResponse(f"{BASE}/api/board/queue", 500)]

    findings = detect_client_errors(responses, allowed={409}, where=WHERE)

    assert [(f.detector, f.url) for f in findings] == [("client-error", f"{BASE}/api/board/queue")]


def test_client_error_negative_allowed_and_success() -> None:
    responses = [HttpResponse(f"{BASE}/api/a", 409), HttpResponse(f"{BASE}/api/b", 200)]

    assert detect_client_errors(responses, allowed={409}, where=WHERE) == []


def test_null_id_positive_for_each_token() -> None:
    requests = [
        HttpRequest("GET", f"{BASE}/api/card/null", {}),
        HttpRequest("GET", f"{BASE}/api/x?id=undefined", {}),
        HttpRequest("GET", f"{BASE}/api/x?id=%5Bobject%20Object%5D", {}),
    ]

    findings = detect_null_ids(requests, WHERE)

    assert len(findings) == 3
    assert {f.detector for f in findings} == {"null-id"}


def test_null_id_negative_clean_url() -> None:
    requests = [HttpRequest("GET", f"{BASE}/api/card/42?nullable=0", {})]

    assert detect_null_ids(requests, WHERE) == []


def test_missing_header_positive_for_mutating_request() -> None:
    requests = [HttpRequest("PUT", f"{BASE}/api/board/queue/move", {})]

    findings = detect_missing_header(requests, BASE, WHERE)

    assert len(findings) == 1
    assert findings[0].detector == "missing-header"
    assert findings[0].url == f"{BASE}/api/board/queue/move"
    assert findings[0].reason == "PUT /api/board/queue/move without X-AHAWR-Dashboard header"


def test_missing_header_negative_read_or_header_present_or_other_origin() -> None:
    requests = [
        HttpRequest("GET", f"{BASE}/api/board", {}),
        HttpRequest("POST", f"{BASE}/api/board/queue", DASHBOARD),
        HttpRequest("DELETE", "http://other.example/api/x", {}),
    ]

    assert detect_missing_header(requests, BASE, WHERE) == []


def test_failed_asset_positive_image_and_stylesheet() -> None:
    failures = [
        FailedRequest(f"{BASE}/static/logo.png", "image"),
        FailedRequest(f"{BASE}/static/board.css", "stylesheet"),
    ]

    findings = detect_failed_assets(failures, WHERE)

    assert [f.detector for f in findings] == ["asset-failed", "asset-failed"]


def test_failed_asset_negative_other_resource_types() -> None:
    failures = [FailedRequest(f"{BASE}/api/board", "fetch")]

    assert detect_failed_assets(failures, WHERE) == []


def test_bad_text_positive_equal_or_contained_token() -> None:
    elements = [
        DomElement("#a", "undefined"),
        DomElement("#b", "Total: NaN items"),
        DomElement("#c", "[object Object]"),
    ]

    findings = detect_bad_text(elements, WHERE)

    assert [f.detector for f in findings] == ["bad-text"] * 3
    assert findings[0].reason == "visible text 'undefined' in #a"


def test_bad_text_negative_similar_words_and_hidden_element() -> None:
    elements = [
        DomElement("#a", "nullable"),
        DomElement("#b", "Nan Smith"),
        DomElement("#c", "undefined", visible=False),
    ]

    assert detect_bad_text(elements, WHERE) == []


def test_hidden_message_positive_text_only_in_hidden_element() -> None:
    appeared = [DomElement("#boardstate", "Saved", visible=False)]

    findings = detect_hidden_messages(appeared, WHERE)

    assert [(f.detector, f.reason) for f in findings] == [
        ("error-visible", "message 'Saved' appeared only in hidden #boardstate")
    ]


def test_hidden_message_negative_visible_or_empty() -> None:
    appeared = [
        DomElement("#msg", "Saved"),
        DomElement("#empty", "  ", visible=False),
    ]

    assert detect_hidden_messages(appeared, WHERE) == []


def test_overflow_positive_wider_than_viewport() -> None:
    elements = [DomElement("#wide", "x", x_px=900, width_px=200)]

    findings = detect_overflow(elements, viewport_width_px=1024, where=WHERE)

    assert [(f.detector, f.reason) for f in findings] == [
        ("overflow", "#wide ends at 1100px, past the 1024px viewport")
    ]


def test_overflow_positive_zero_size_in_visible_container() -> None:
    elements = [DomElement("#empty", "x", width_px=0)]

    findings = detect_overflow(elements, viewport_width_px=1024, where=WHERE)

    assert [f.reason for f in findings] == ["zero-size #empty inside a visible container"]


def test_overflow_negative_fits_or_zero_size_in_hidden_container() -> None:
    elements = [
        DomElement("#fits", "x", x_px=0, width_px=1024),
        DomElement("#zero", "x", width_px=0, parent_visible=False),
    ]

    assert detect_overflow(elements, viewport_width_px=1024, where=WHERE) == []


def test_untranslated_key_positive_key_in_one_language_only() -> None:
    english = [DomElement("#tab", "conflict.plan_changed")]
    russian = [DomElement("#tab", "План изменился")]

    findings = detect_untranslated_keys(english, russian, WHERE)

    assert [f.detector for f in findings] == ["bad-text"]
    assert "en shows i18n key 'conflict.plan_changed' in #tab" in findings[0].reason


def test_untranslated_key_positive_russian_side_shows_key() -> None:
    english = [DomElement("#tab", "Plan changed")]
    russian = [DomElement("#tab", "conflict.plan_changed")]

    findings = detect_untranslated_keys(english, russian, WHERE)

    assert len(findings) == 1
    assert "ru shows i18n key" in findings[0].reason


def test_untranslated_key_negative_no_translation_at_same_element() -> None:
    english = [DomElement("#a", "conflict.plan_changed"), DomElement("#b", "Plain")]
    russian = [DomElement("#b", "Простой"), DomElement("#a", "conflict.plan_changed")]
    other_element = [DomElement("#c", "План")]

    assert detect_untranslated_keys(english, russian, WHERE) == []
    assert detect_untranslated_keys(english, other_element, WHERE) == []


def test_no_effect_positive_warning_when_click_changed_nothing() -> None:
    snapshot = [DomElement("#tab", "Board")]

    findings = detect_no_effect(snapshot, list(snapshot), disabled=False, where=WHERE)

    assert [(f.detector, f.severity) for f in findings] == [("no-effect", WARNING)]


def test_no_effect_negative_changed_or_disabled() -> None:
    before = [DomElement("#tab", "Board")]
    after = [DomElement("#tab", "Lanes")]

    assert detect_no_effect(before, after, disabled=False, where=WHERE) == []
    assert detect_no_effect(before, list(before), disabled=True, where=WHERE) == []
