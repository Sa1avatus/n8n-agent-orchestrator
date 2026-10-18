"""Build the AHAWR logo set into docs/img: banner.svg, logo.svg (rounded dark tile), mark.svg (transparent), favicon.svg and flow.svg
(the "How a mission runs" scheme in the same colours).

    python scripts/make_logo.py

The mark: six gears of one size stand in a ring and mesh with their neighbours (the last one with the first, so the ring turns:
neighbours go in opposite directions). The axis in the middle is n8n, the loop that drives them. One gear per process:
Missions (blue), Architect (amber), Retrieval (teal), Worker (orange), Reviewer (violet), Result (green).
The legend text is abstract on purpose: it names roles, not models.
"""
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "img")
BLU, AMB, TEAL, ORA, VIO, GRN = "#6aa8ff", "#ffb454", "#4fd1c5", "#ff8a3d", "#b39bff", "#5cc98b"
AXIS, GRY, CARD, LINE, TXT, DARK = "#eceef3", "#a3a9b8", "#20232b", "#33384a", "#eceef3", "#10131a"
FONT = "Segoe UI, Helvetica, Arial, sans-serif"
MOD = 6.4          # tooth module: the same for every gear so that they mesh
N_TEETH = 9
# key, colour, title, legend line (abstract: roles, not models), pictogram
ROLES = (
    ("M", BLU, "Missions", "the queue of work: goal, rules, acceptance criteria", "queue"),
    ("A", AMB, "Architect", "plans a mission as small tasks", "plan"),
    ("Q", TEAL, "Retrieval", "finds the context the roles need", "doc"),
    ("W", ORA, "Worker", "builds one task at a time", "code"),
    ("R", VIO, "Reviewer", "scores the result: retry with the review, or next task", "lens"),
    ("X", GRN, "Result", "the report for the operator", "check"),
)
AXIS_LINE = ("Axis: n8n", "the loop: queue, retries, state")

DEFS = f'''<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#17191f"/><stop offset="1" stop-color="#2b2113"/></linearGradient>
<linearGradient id="amber" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ffc766"/><stop offset="1" stop-color="#ff8a3d"/></linearGradient>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#fff" stroke-opacity=".05"/></pattern>
<marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{GRY}"/></marker>
<marker id="b" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{VIO}"/></marker>
<marker id="g" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{GRN}"/></marker>'''


# ------------------------------------------------------------------ gear geometry
def pt(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def rr(n):
    return n * MOD / 2


def profile(cx, cy, n, rot):
    r = rr(n)
    r_out, r_root = r + MOD * 0.5, r - MOD * 0.5
    p = 360.0 / n
    pts = []
    for k in range(n):
        c = rot + k * p
        for dr, da in ((r_root, -0.30), (r_out, -0.17), (r_out, 0.17), (r_root, 0.30), (r_root, 0.5)):
            pts.append(pt(cx, cy, dr, c + da * p))
    return " ".join("%.1f,%.1f" % q for q in pts)


def offset(phi, theta, p):
    return (((theta - phi) % p) / p) % 1.0


def mesh_next(phi_i, theta, n_i, n_j):
    p_i, p_j = 360.0 / n_i, 360.0 / n_j
    o = offset(phi_i, theta, p_i)
    tooth = o < 1e-3 or o > 1 - 1e-3
    return (theta + 180 - (0.5 * p_j if tooth else 0.0)) % p_j


def ring(n_gears, n_teeth, start=-120.0):
    """centres and rotations of gears of one size meshed in a closed ring (the radius of the ring follows from the module)"""
    r = rr(n_teeth)
    rc = r / math.sin(math.pi / n_gears)             # the neighbours touch at their pitch circles
    cs = [pt(0, 0, rc, start + i * 360.0 / n_gears) for i in range(n_gears)]
    rot = [0.0] * n_gears
    th = math.degrees(math.atan2(cs[1][1] - cs[0][1], cs[1][0] - cs[0][0]))
    rot[0] = th % (360.0 / n_teeth)
    for i in range(1, n_gears):
        th = math.degrees(math.atan2(cs[i][1] - cs[i - 1][1], cs[i][0] - cs[i - 1][0]))
        rot[i] = mesh_next(rot[i - 1], th, n_teeth, n_teeth)
    # the closing link must mesh too (checked, an even number of gears with equal teeth always does)
    th = math.degrees(math.atan2(cs[0][1] - cs[-1][1], cs[0][0] - cs[-1][0]))
    o1, o2 = offset(rot[-1], th, 360.0 / n_teeth), offset(rot[0], th + 180, 360.0 / n_teeth)
    ok = (min(o1, 1 - o1) < 0.02 and abs(o2 - 0.5) < 0.02) or (abs(o1 - 0.5) < 0.02 and min(o2, 1 - o2) < 0.02)
    assert ok, "the ring does not close: %s %s" % (o1, o2)
    return rc, cs, rot


# ------------------------------------------------------------------ small drawing helpers
def head(x, y, dx, dy, color, sw):
    n = math.hypot(dx, dy)
    dx, dy = dx / n, dy / n
    nx, ny = -dy, dx
    w = sw / 2 + 2.0
    pts = [(x + dx * 7.0, y + dy * 7.0), (x - dx * 1.0 + nx * w, y - dy * 1.0 + ny * w), (x - dx * 1.0 - nx * w, y - dy * 1.0 - ny * w)]
    return '<polygon points="%s" fill="%s"/>' % (" ".join("%.1f,%.1f" % q for q in pts), color)


def arc_arrow(cx, cy, r, a1, a2, color, sw):
    x1, y1 = pt(cx, cy, r, a1)
    x2, y2 = pt(cx, cy, r, a2)
    sweep = 1 if a2 > a1 else 0
    s = 1 if a2 > a1 else -1
    t = f'<path d="M{x1:.1f} {y1:.1f} A{r:.1f} {r:.1f} 0 0 {sweep} {x2:.1f} {y2:.1f}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="round"/>'
    return t + head(x2, y2, -math.sin(math.radians(a2)) * s, math.cos(math.radians(a2)) * s, color, sw)


def icon(kind, x, y, s, color):
    w = max(1.8, s * 0.17)
    if kind == "queue":
        return "".join(f'<rect x="{x - s * 0.62 + i * s * 0.26:.1f}" y="{y - s * 0.42 + i * s * 0.30:.1f}" width="{s * 0.74:.1f}" height="{s * 0.34:.1f}" rx="{s * 0.08:.1f}" fill="{DARK}" stroke="{color}" stroke-width="{w:.1f}"/>' for i in range(3))
    if kind == "plan":
        return (f'<path d="M{x:.1f} {y - s * 0.3:.1f}V{y:.1f}M{x - s * 0.5:.1f} {y + s * 0.34:.1f}V{y:.1f}H{x + s * 0.5:.1f}V{y + s * 0.34:.1f}" stroke="{color}" stroke-width="{w:.1f}" fill="none" stroke-linejoin="round"/>'
                f'<circle cx="{x:.1f}" cy="{y - s * 0.42:.1f}" r="{s * 0.18:.1f}" fill="{color}"/><circle cx="{x - s * 0.5:.1f}" cy="{y + s * 0.5:.1f}" r="{s * 0.16:.1f}" fill="{color}"/>'
                f'<circle cx="{x + s * 0.5:.1f}" cy="{y + s * 0.5:.1f}" r="{s * 0.16:.1f}" fill="{color}"/>')
    if kind == "doc":
        return (f'<rect x="{x - s * 0.5:.1f}" y="{y - s * 0.6:.1f}" width="{s:.1f}" height="{s * 1.2:.1f}" rx="{s * 0.12:.1f}" fill="none" stroke="{color}" stroke-width="{w:.1f}"/>'
                f'<path d="M{x - s * 0.25:.1f} {y - s * 0.2:.1f}H{x + s * 0.25:.1f}M{x - s * 0.25:.1f} {y + s * 0.15:.1f}H{x + s * 0.25:.1f}" stroke="{color}" stroke-width="{w:.1f}" stroke-linecap="round"/>')
    if kind == "code":
        return (f'<path d="M{x - s * 0.28:.1f} {y - s * 0.42:.1f}L{x - s * 0.7:.1f} {y:.1f}L{x - s * 0.28:.1f} {y + s * 0.42:.1f}M{x + s * 0.28:.1f} {y - s * 0.42:.1f}L{x + s * 0.7:.1f} {y:.1f}L{x + s * 0.28:.1f} {y + s * 0.42:.1f}M{x + s * 0.12:.1f} {y - s * 0.5:.1f}L{x - s * 0.12:.1f} {y + s * 0.5:.1f}" '
                f'stroke="{color}" stroke-width="{w:.1f}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
    if kind == "lens":
        return (f'<circle cx="{x - s * 0.12:.1f}" cy="{y - s * 0.12:.1f}" r="{s * 0.44:.1f}" fill="none" stroke="{color}" stroke-width="{w:.1f}"/>'
                f'<path d="M{x + s * 0.22:.1f} {y + s * 0.22:.1f}L{x + s * 0.62:.1f} {y + s * 0.62:.1f}" stroke="{color}" stroke-width="{w * 1.3:.1f}" stroke-linecap="round"/>')
    if kind == "check":
        return f'<path d="M{x - s * 0.5:.1f} {y + s * 0.02:.1f}L{x - s * 0.12:.1f} {y + s * 0.4:.1f}L{x + s * 0.55:.1f} {y - s * 0.4:.1f}" stroke="{color}" stroke-width="{w * 1.2:.1f}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    return ""


def mark_svg(detail=True):
    """the mark in a 200 x 200 box: the ring of six gears and the n8n axis"""
    n = len(ROLES)
    rc, cs, rot = ring(n, N_TEETH)
    r = rr(N_TEETH)
    extent = rc + r + MOD
    sc = (200 - 12) / (2 * extent)
    out = f'<g transform="translate(100 100) scale({sc:.3f})">'
    axis_r = rc - r - MOD * 0.5 - 7
    for (x, y), (key, color, *_rest) in zip(cs, ROLES):
        L = math.hypot(x, y)
        out += f'<path d="M{x / L * axis_r:.1f} {y / L * axis_r:.1f}L{x / L * (L - r * 0.52):.1f} {y / L * (L - r * 0.52):.1f}" stroke="{AXIS}" stroke-width="2.6" stroke-opacity=".5" stroke-linecap="round"/>'
    for i, ((x, y), (key, color, title, line, kind)) in enumerate(zip(cs, ROLES)):
        out += f'<polygon points="{profile(x, y, N_TEETH, rot[i])}" fill="{CARD}" stroke="{color}" stroke-width="{3 if detail else 4.2}" stroke-linejoin="round"/>'
        out += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * 0.52:.1f}" fill="{DARK}" stroke="{color}" stroke-width="2.4"/>'
        if detail:
            out += icon(kind, x, y, r * 0.34, color)
            # the turning direction: inside the gear, clear of the hub and of the teeth roots
            out += arc_arrow(x, y, r * 0.71, 200 if i % 2 == 0 else 340, 305 if i % 2 == 0 else 235, color, 2.1)
    out += f'<circle cx="0" cy="0" r="{axis_r:.1f}" fill="{DARK}" stroke="{AXIS}" stroke-width="3.2"/>'
    if detail:
        out += f'<text x="0" y="{axis_r * 0.2:.1f}" text-anchor="middle" fill="{AXIS}" font-family="{FONT}" font-size="{max(14, axis_r * 0.62):.0f}" font-weight="800">n8n</text>'
    else:
        out += f'<circle cx="0" cy="0" r="{axis_r * 0.42:.1f}" fill="{AXIS}"/>'
    return out + "</g>"


def legend():
    rows = [(AXIS, AXIS_LINE[0], AXIS_LINE[1])] + [(c, t, d) for _, c, t, d, _ in ROLES]
    step, hh = 35, 31
    out = ""
    for i, (color, title, line) in enumerate(rows):
        y = i * step
        out += f'<rect x="250" y="{y}" width="320" height="{hh}" rx="10" fill="#1b1e26" stroke="{LINE}"/><rect x="250" y="{y}" width="6" height="{hh}" rx="3" fill="{color}"/>'
        out += f'<text x="268" y="{y + 13}" fill="{color}" font-family="{FONT}" font-size="12" font-weight="700">{title}</text>'
        out += f'<text x="268" y="{y + 25}" fill="{GRY}" font-family="{FONT}" font-size="9.5" font-weight="500">{line}</text>'
    return out


def banner():
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 300" role="img" aria-label="AHAWR: Agentic Hub for Automation Workflow Routing"><defs>{DEFS}</defs>
<rect width="1200" height="300" rx="22" fill="url(#bg)"/><rect width="1200" height="300" rx="22" fill="url(#grid)"/>
<rect x="1.5" y="1.5" width="1197" height="297" rx="21" fill="none" stroke="#ffc766" stroke-opacity=".55" stroke-width="3"/>
<text x="60" y="130" font-family="{FONT}" font-size="96" font-weight="800" fill="url(#amber)" letter-spacing="4">AHAWR</text>
<text x="64" y="176" font-family="{FONT}" font-size="22" fill="{TXT}">Agentic Hub for Automation Workflow Routing</text>
<text x="64" y="212" font-family="{FONT}" font-size="16" fill="{GRY}">Missions in, reviewed code out.</text>
<text x="64" y="236" font-family="{FONT}" font-size="16" fill="{GRY}">n8n drives the loop, claude-runner runs the models.</text>
<g transform="translate(588 30)">{mark_svg()}</g>
<g transform="translate(600 34)">{legend()}</g></svg>'''


def tile(bg=True, detail=True, size=200):
    back = f'<rect width="200" height="200" rx="28" fill="#17191f"/>' if bg else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="{size}" height="{size}" role="img" aria-label="AHAWR"><defs>{DEFS}</defs>{back}{mark_svg(detail)}</svg>'


def flow():
    """How a mission runs, in the colours of the gears"""
    def box(x, y, w, h, color, title, sub):
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{CARD}" stroke="{color}" stroke-width="2"/>'
                f'<text x="{x + w / 2}" y="{y + h / 2 - 2}" fill="{color}" font-size="17" font-weight="700">{title}</text>'
                f'<text x="{x + w / 2}" y="{y + h / 2 + 17}" fill="{GRY}" font-size="12.5" font-weight="400">{sub}</text>')
    top = ((BLU, "Missions", "queue, rules, criteria"), (AMB, "Architect", "small tasks"), (ORA, "Worker", "builds one task"), (VIO, "Reviewer", "score and verdict"), (GRN, "Result", "report, notices"))
    xs = (30, 235, 440, 645, 850)
    out = "".join(box(x, 40, 170, 62, c, t, s) for x, (c, t, s) in zip(xs, top))
    arrows = "".join(f'<path d="M{xs[i] + 170} 71H{xs[i + 1] - 2}" marker-end="url(#a)"/>' for i in range(3))
    arrows += f'<path d="M{xs[3] + 170} 71H{xs[4] - 2}" stroke="{GRN}" marker-end="url(#g)"/>'
    retry = f'<path d="M730 102V150H525V104" stroke="{VIO}" stroke-dasharray="6 5" marker-end="url(#b)"/>'
    out += f'<g stroke="{GRY}" stroke-width="2.5" fill="none" stroke-linecap="round">{arrows}{retry}</g>'
    out += f'<text x="628" y="144" fill="{VIO}" font-size="12.5" font-weight="600">retry with the review</text>'
    out += box(292, 116, 140, 50, TEAL, "Retrieval", "context for the roles")
    out += f'<path d="M432 141H452V104" stroke="{TEAL}" stroke-width="2" stroke-dasharray="3 4" fill="none" marker-end="url(#a)"/>'
    layers = ((AXIS, "n8n: the axis", "the loop, queue, retries, state"), (GRY, "claude-runner", "runs the roles, sessions"),
              (GRY, "Models", "local and cloud"), (GRY, "Dashboard + Telegram", "runs, mission board, notices"))
    for i, (c, t, s) in enumerate(layers):
        x = 30 + i * 262
        out += (f'<rect x="{x}" y="206" width="250" height="56" rx="12" fill="#1b1e26" stroke="{LINE}"/><rect x="{x}" y="206" width="6" height="56" rx="3" fill="{c}"/>'
                f'<text x="{x + 20}" y="229" text-anchor="start" fill="{c if c != GRY else TXT}" font-size="14" font-weight="700">{t}</text>'
                f'<text x="{x + 20}" y="248" text-anchor="start" fill="{GRY}" font-size="11.5" font-weight="500">{s}</text>')
    out += f'<text x="550" y="296" fill="{GRY}" font-size="12.5">the loop runs in n8n; every role runs through claude-runner</text>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 310" role="img" aria-label="How a mission runs"><defs>{DEFS}</defs>
<rect width="1100" height="310" rx="18" fill="#17191f"/><rect x="1" y="1" width="1098" height="308" rx="17" fill="none" stroke="{LINE}"/>
<g font-family="{FONT}" text-anchor="middle">{out}</g></svg>'''


def main():
    os.makedirs(OUT, exist_ok=True)
    files = {"banner.svg": banner(), "logo.svg": tile(True, True), "mark.svg": tile(False, True), "favicon.svg": tile(True, False, 64), "flow.svg": flow()}
    for name, text in files.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
        print("wrote docs/img/" + name)


if __name__ == "__main__":
    main()
