from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .test_dashboard import dashboard


def test_ops_tab_off_leaves_the_page_unchanged(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With DASHBOARD_OPS_TAB unset the served page has no Control tab and no
    ops-tab markers."""
    monkeypatch.delenv("DASHBOARD_OPS_TAB", raising=False)
    with dashboard(client) as dash:
        text = dash.get("/").text
        assert "board_control" not in text
        assert "controlpane" not in text
        assert "tabcontrol" not in text
        assert "<!--ops-tab-->" not in text


def test_ops_tab_on_injects_the_tab_and_its_assets(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With DASHBOARD_OPS_TAB=1 the page has the tab button, the pane, the css
    link, and the three static assets (each referenced once), and the markers
    are gone."""
    monkeypatch.setenv("DASHBOARD_OPS_TAB", "1")
    with dashboard(client) as dash:
        text = dash.get("/").text
        for needle in (
            'pane.id = "controlpane"',
            'btn.id = "tabcontrol"',
            "/static/board_control_view.js",
            "/static/board_control.js",
            "/static/board_control.css",
        ):
            assert text.count(needle) == 1, needle
        assert "<!--ops-tab-->" not in text
        assert dash.get("/static/board_control_view.js").status_code == 200
        assert dash.get("/static/board_control.js").status_code == 200
        assert dash.get("/static/board_control.css").status_code == 200


def test_ops_tab_zero_is_off(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """DASHBOARD_OPS_TAB=0 is unset-equivalent: no tab on the page."""
    monkeypatch.setenv("DASHBOARD_OPS_TAB", "0")
    with dashboard(client) as dash:
        assert "controlpane" not in dash.get("/").text


def test_ops_tab_unset_page_equals_explicit_off_baseline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page served with the flag unset is byte-identical to the page served
    with the flag explicitly off."""
    monkeypatch.delenv("DASHBOARD_OPS_TAB", raising=False)
    with dashboard(client) as dash:
        unset_page = dash.get("/").text
    monkeypatch.setenv("DASHBOARD_OPS_TAB", "0")
    with dashboard(client) as dash:
        baseline_page = dash.get("/").text
    assert unset_page == baseline_page
