"""Render the Guards detail-view stand-in PNG at the contract viewport (1280x800).

Uses a local TTF (Liberation Sans) via @font-face data URI + document.fonts.ready,
because the environment has no system fonts installed. Mirrors the layout in
board_guards.js:54-156 and board_guards.css:2-59 against dashboard.html:18-28 vars.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONT_DIR = Path("/tmp/ahawr-libs/extracted/usr/share/fonts/truetype/liberation")
REGULAR_TTF = FONT_DIR / "LiberationSans-Regular.ttf"
BOLD_TTF = FONT_DIR / "LiberationSans-Bold.ttf"
OUTPUT_PNG = HERE / "guards-reference.png"
VIEWPORT = {"width": 1280, "height": 800}

GUARD_TEXT = {
    "queue_restart": "Restarts the mission queue when it stops",
    "host_heartbeat": "Watches the host watchers",
    "tg_relay": "Forwards notices to Telegram",
    "mission_notice": "Posts mission start and end notices",
    "idle": "Stops idle runs after a timeout",
}

GUARDS = [
    {"name": "queue_restart", "status": "ok", "dry": False, "interval_s": 30, "health_window": "24h",
     "last_run": 1800, "health": {"ok_pct": 98, "checks": 287, "errors": 2},
     "error_count": 2,
     "strip": ["ok"] * 38 + ["warning", "ok", "ok", "error", "ok", "ok", "ok", "ok"],
     "actions": [("12:45:03", "restarted queue"), ("11:30:11", "sent start notice")]},
    {"name": "host_heartbeat", "status": "warning", "dry": False, "interval_s": 60, "health_window": "24h",
     "last_run": 3100, "health": {"ok_pct": 84, "checks": 144, "errors": 5},
     "error_count": 5,
     "strip": ["ok"] * 20 + ["warning"] * 5 + ["ok"] * 7 + ["warning", "ok", "ok"],
     "actions": [("14:02:19", "host watcher stale, reset")]},
    {"name": "tg_relay", "status": "error", "dry": True, "interval_s": 30, "health_window": "1h",
     "last_run": 7200, "health": {"ok_pct": 0, "checks": 3, "errors": 3},
     "error_count": 3,
     "strip": ["none", "none", "error", "error", "error", "ok", "none", "none", "error", "none"],
     "actions": [("09:10:22", "token refresh failed")]},
    {"name": "mission_notice", "status": "ok", "dry": False, "interval_s": 120, "health_window": "7d",
     "last_run": 3600, "health": {"ok_pct": 100, "checks": 92, "errors": 0},
     "error_count": 0,
     "strip": ["ok"] * 48,
     "actions": []},
    {"name": "idle", "status": "off", "dry": False, "interval_s": 60, "health_window": "24h",
     "last_run": 0, "health": None, "error_count": 0,
     "strip": ["none"] * 48,
     "actions": []},
]


def _font_face(family: str, url: str) -> str:
    return f"""
    @font-face {{
      font-family: '{family}';
      src: url('{url}') format('truetype');
      font-weight: {'bold' if 'Bold' in family else 'normal'};
      font-display: swap;
    }}"""


def build_html() -> str:
    reg_b64 = base64.b64encode(REGULAR_TTF.read_bytes()).decode()
    bold_b64 = base64.b64encode(BOLD_TTF.read_bytes()).decode()
    reg_url = f"data:font/ttf;base64,{reg_b64}"
    bold_url = f"data:font/ttf;base64,{bold_b64}"

    # Build guard list items (left panel)
    items_html = ""
    for g in GUARDS:
        cls = g["status"] if g["status"] != "off" else "off"
        items_html += (
            f'<button type="button" class="gt-item" data-select="{g["name"]}">'
            f'<span class="gt-led {cls}"></span>{g["name"]}</button>\n'
        )

    # Selected guard = first (queue_restart)
    g = GUARDS[0]
    status_cls = g["status"] if g["status"] != "off" else "off"
    shape = {"ok": "●", "warning": "▲", "error": "✕", "off": "○"}.get(g["status"], "○")
    status_word = "disabled" if g["status"] == "off" else g["status"]
    on = g["status"] != "off"
    dry_mark = ' <span class="gt-mark">dry run</span>' if g["dry"] else ""

    # Toggle switch (disabled because DASHBOARD_GUARDS_EDIT is off)
    toggle = (
        f'<button type="button" class="gt-tog{" on" if on else ""}" '
        f'role="switch" aria-checked="{str(on).lower()}" '
        f'aria-label="{g["name"]}" disabled></button>'
    )

    # Description line
    desc = GUARD_TEXT[g["name"]]
    when = f"{g["last_run"] // 60} min ago" if g["last_run"] > 0 else "—"

    # Settings card (read-only)
    settings = (
        '<div class="gt-card gt-block">'
        '<h4>Settings</h4>'
        f'<div class="gt-sub">Poll interval: {g["interval_s"]} s</div>'
        f'<div class="gt-sub">Health window: {g["health_window"]}</div>'
        '<div class="gt-sub">Editing is off (DASHBOARD_GUARDS_EDIT).</div>'
        '</div>'
    )

    # Health card
    if g["health"]:
        pct = f'{g["health"]["ok_pct"]}%'
        counts = f'{g["health"]["checks"]} checks · {g["health"]["errors"]} error · in {g["health_window"]}'
    else:
        pct = "—"
        counts = ""
    strip_html = ""
    for s in g["strip"]:
        strip_html += f'<i class="{s}"></i>'
    health = (
        '<div class="gt-card gt-block">'
        '<h4>Health</h4>'
        f'<div class="gt-big">{pct}</div>'
        f'<div class="gt-sub">{counts}</div>'
        f'<div class="gt-strip">{strip_html}</div>'
        '</div>'
    )

    # Recent actions card
    actions_html = ""
    for t, txt in g["actions"]:
        actions_html += f'<div class="gt-sub gt-mono">{t} {txt}</div>'
    if not actions_html:
        actions_html = '<div class="gt-sub">—</div>'
    actions = (
        '<div class="gt-card gt-block">'
        '<h4>Recent actions</h4>'
        f'{actions_html}'
        '</div>'
    )

    # Detail header
    detail = (
        f'<div class="gt-dhead">'
        f'<b class="gt-dname">{g["name"]}</b>'
        f'<span class="gt-st {status_cls}"><span aria-hidden="true">{shape}</span> {status_word}</span>'
        f'{dry_mark}'
        f'<span class="gt-push">{toggle}</span>'
        f'</div>\n'
        f'<div class="gt-sub">{desc} · last: {when}</div>\n'
        f'{settings}{health}{actions}'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AHAWR Guars detail</title>
<style>
{_font_face('Liberation Sans', reg_url)}
{_font_face('Liberation Sans Bold', bold_url)}
@font-face {{ font-family: 'Liberation Mono'; src: url('{bold_url}') format('truetype'); font-weight: normal; font-display: swap; }}

:root {{
  color-scheme: light;
  --bg: #f7f8fa; --panel: #ffffff; --ink: #1b2130; --muted: #6a7385; --faint: #687182;
  --line: #e6e8ee; --hover: #f2f4f8; --sel: #e9f0fd; --code: #f3f5f8; --accent: #2f6fde;
  --ok: #16803c; --err: #d03b30; --retry: #b98900; --shadow: rgba(0,0,0,.15);
  --mono: 'Liberation Mono', 'Liberation Sans', monospace;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; height: 100%; width: 100%; }}
body {{
  background: var(--bg); color: var(--ink);
  font: 400 13.5px/1.45 'Liberation Sans', -apple-system, "Segoe UI", Roboto, sans-serif;
  overflow: hidden;
}}
button, select, input {{ font: inherit; color: inherit; }}

{GUARD_CSS}
</style>
</head>
<body>
<div id="content" style="flex:1; display:flex; flex-direction:column; min-width:0; min-height:0;">
  <!-- tab bar (Runs / Missions / Providers / Guards) -->
  <nav class="tabs" id="viewtabs" style="background:var(--panel); border-bottom:1px solid var(--line);">
    <button id="tabruns" type="button">Runs</button>
    <button id="tabboard" type="button">Missions</button>
    <button id="tabprov" type="button">Providers</button>
    <button id="tabguards" type="button" class="on">Guards</button>
  </nav>
  <!-- guards page -->
  <main id="main" style="flex:1; overflow:auto; min-width:0;">
    <div id="guardspane" style="padding:12px 16px;">
      <!-- page header -->
      <div class="gt-head">
        <b>guards on</b> · 3 ok · 1 warning · 1 error · 1 disabled
      </div>
      <!-- view tabs -->
      <div class="gt-views">
        <button type="button" class="on">Detail</button>
        <button type="button">Cards</button>
        <button type="button">Flow</button>
        <button type="button">Timelines</button>
      </div>
      <!-- split detail view -->
      <div class="gt-split">
        <div class="gt-side">
{items_html}
        </div>
        <div class="gt-main">
{detail}
        </div>
      </div>
    </div>
  </main>
</div>
<script>
document.fonts.ready.then(() => {{ window.__fontsReady = true; }});
</script>
</body>
</html>"""


GUARD_CSS = """\
/* board_guards.css */
#guardspane { flex: 1; overflow: auto; padding: 12px 16px; }
#guardspane[hidden] { display: none; }
.gt-head { font-size: 13px; color: var(--muted); margin-bottom: 10px; }
.gt-head b { color: var(--ink); }
.gt-views, .gt-windows { display: flex; gap: 6px; margin-bottom: 12px; }
.gt-views button, .gt-windows button { border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--muted); cursor: pointer; padding: 3px 12px; font-size: 12px; }
.gt-views button.on, .gt-windows button.on { background: var(--hover); color: var(--ink); font-weight: 600; }
.gt-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; }
.gt-card { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: var(--panel); min-width: 0; }
.gt-name { font-size: 13px; overflow-wrap: anywhere; }
.gt-sub { font-size: 12px; color: var(--muted); margin-top: 3px; overflow-wrap: anywhere; }
.gt-mark { font-size: 11px; color: var(--muted); font-style: italic; }
.gt-sec { font-size: 13px; margin: 16px 0 8px; color: var(--muted); font-weight: 600; }
.gt-st { font-size: 12px; font-weight: 600; white-space: nowrap; }
.gt-st.ok { color: var(--ok); } .gt-st.warning { color: var(--retry); } .gt-st.error { color: var(--err); } .gt-st.off { color: var(--muted); }
.gt-strip { display: flex; gap: 2px; margin-top: 8px; }
.gt-strip i { flex: 1; height: 14px; border-radius: 2px; background: var(--line); }
.gt-strip i.ok { background: var(--ok); } .gt-strip i.warning { background: var(--retry); } .gt-strip i.error { background: var(--err); }
.gt-flow svg { width: 100%; max-width: 680px; height: auto; }
.gt-colhead { font-size: 12px; fill: var(--muted); }
.gt-wire { stroke: var(--line); stroke-width: 1.5; }
.gt-wire.lit { stroke: var(--ok); stroke-width: 2.5; }
.gt-node rect { fill: var(--panel); stroke: var(--line); stroke-width: 1.5; }
.gt-node text { font-size: 12px; fill: var(--ink); }
.gt-node.ok rect { stroke: var(--ok); } .gt-node.warning rect { stroke: var(--retry); } .gt-node.error rect { stroke: var(--err); stroke-dasharray: 4 3; }
.gt-lane { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-top: 1px solid var(--line); }
.gt-lname { width: 190px; flex: none; font-size: 12px; overflow-wrap: anywhere; }
.gt-track { position: relative; flex: 1; height: 20px; background: var(--panel); border-radius: 4px; }
.gt-dot { position: absolute; top: 6px; width: 8px; height: 8px; margin-left: -4px; border-radius: 50%; background: var(--ok); }
.gt-dot.warning { background: var(--retry); } .gt-dot.error { background: var(--err); border-radius: 2px; }
.gt-dot.act { outline: 2px solid var(--ink); outline-offset: 1px; }
@media (max-width: 520px) { .gt-lane { flex-direction: column; align-items: stretch; gap: 4px; } .gt-lname { width: auto; } }
.gt-split { display: grid; grid-template-columns: 230px minmax(0, 1fr); gap: 16px; align-items: start; }
.gt-side { border: 1px solid var(--line); border-radius: 12px; padding: 8px; background: var(--panel); }
.gt-item { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; font: inherit; font-size: 13px; color: var(--ink); background: none; border: 1px solid transparent; border-radius: 8px; padding: 8px 10px; cursor: pointer; }
.gt-item.on { background: var(--sel); color: var(--accent); }
.gt-led { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); flex: none; }
.gt-led.ok { background: var(--ok); } .gt-led.warning { background: var(--retry); } .gt-led.error { background: var(--err); }
.gt-main { min-width: 0; }
.gt-dhead { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.gt-dname { font-size: 20px; font-weight: 600; font-family: var(--mono); }
.gt-push { margin-left: auto; }
.gt-tog { width: 40px; height: 22px; border-radius: 99px; border: none; background: var(--line); position: relative; cursor: pointer; padding: 0; }
.gt-tog::after { content: ""; position: absolute; top: 3px; left: 3px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: left .15s; }
.gt-tog.on { background: var(--accent); } .gt-tog.on::after { left: 21px; }
.gt-tog:disabled { opacity: .5; cursor: default; }
.gt-block { margin-top: 12px; padding: 14px 16px; border-radius: 12px; background: var(--panel); border: 1px solid var(--line); }
.gt-block h4 { margin: 0 0 8px; font-size: 14px; }
.gt-fields { display: flex; flex-wrap: wrap; gap: 16px; }
.gt-fields label { display: flex; flex-direction: column; gap: 4px; font-size: 12.5px; color: var(--muted); }
.gt-fields input, .gt-fields select { font: inherit; font-size: 13px; color: var(--ink); background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; min-width: 120px; }
.gt-unit { display: flex; align-items: center; gap: 6px; color: var(--ink); }
.gt-unit input { width: 110px; min-width: 0; }
.gt-save { margin-top: 12px; font: inherit; font-size: 13px; color: var(--bg); background: var(--accent); border: none; border-radius: 8px; padding: 6px 16px; cursor: pointer; }
.gt-big { font-size: 24px; font-weight: 600; }
.gt-mono { font-family: var(--mono); }
@media (max-width: 760px) { .gt-split { grid-template-columns: 1fr; } }

/* tabs from dashboard.html:197-199 */
.tabs { display: flex; gap: 14px; padding: 0 14px; border-bottom: 1px solid var(--line); background: var(--panel); }
.tabs button { border: 0; background: none; padding: 8px 0; cursor: pointer; color: var(--muted); border-bottom: 2px solid transparent; font-size: 13px; }
.tabs button.on { color: var(--accent); border-bottom-color: var(--accent); }
"""


def render() -> None:
    from playwright.sync_api import sync_playwright

    html = build_html()
    import os
    os.environ["LD_LIBRARY_PATH"] = os.environ.get("LD_LIBRARY_PATH", "") + (
        ":/tmp/ahawr-libs/extracted/usr/lib/x86_64-linux-gnu"
        ":/tmp/ahawr-libs/extracted/lib/x86_64-linux-gnu"
    )
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.set_content(html, wait_until="networkidle")
        # Wait for fonts to be ready so text is actually rasterized
        page.wait_for_function("window.__fontsReady === true", timeout=15_000)
        page.wait_for_timeout(500)  # extra settle
        page.screenshot(path=str(OUTPUT_PNG), full_page=False)
        browser.close()
    print(f"Wrote {OUTPUT_PNG} ({OUTPUT_PNG.stat().st_size} bytes)")


if __name__ == "__main__":
    sys.exit(render())
