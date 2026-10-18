"""Build ROADMAP.md and docs/roadmap.html from docs/roadmap.json (the one source of the roadmap).

    python scripts/make_roadmap.py          # writes ROADMAP.md and docs/roadmap.html, and the two marked blocks of README.md
    python scripts/make_roadmap.py --check  # exits 1 when a generated file is out of date
    python scripts/make_roadmap.py --from-queue  # first sets "Missions completed" and "Missions in the queue" in docs/roadmap.json
                                                 # from the mission queue and the Live State table (operator machine only: needs data-tables/ops)

Edit docs/roadmap.json (phases, hero numbers, open decisions) in the same commit as the CHANGELOG.md entry, then run this.
The page is self-contained (no external fonts or scripts), dark Graphite-and-Amber with a light variant for light systems.
"""
import html
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "roadmap.json")
TEMPLATE = os.path.join(ROOT, "scripts", "roadmap_template.html")
OUT_HTML = os.path.join(ROOT, "docs", "roadmap.html")
OUT_MD = os.path.join(ROOT, "ROADMAP.md")
README = os.path.join(ROOT, "README.md")
NL, CRLF = chr(10), chr(13) + chr(10)
MARK = {"ok": "[x]", "go": "[~]", "todo": "[ ]"}
STATE = {"done": "Done", "cur": "In progress", "next": "Next", "later": "Later"}


def percent(m):
    return round(m[0] * 100 / m[1]) if m[1] else 0


def render_html(d):
    page = open(TEMPLATE, encoding="utf-8").read()
    e = html.escape
    stats = "".join('<div class="stat"><small>%s</small><b>%s</b></div>' % (e(k), e(v)) for k, v in d["hero"]["stats"])
    openq = "".join("<li>%s</li>" % e(x) for x in d["open"])
    fill = {"{{TITLE}}": e(d["title"]), "{{SUBTITLE}}": e(d["subtitle"]), "{{LEAD}}": e(d["lead"]), "{{NOTE}}": e(d["note"]),
            "{{HERO_TITLE}}": e(d["hero"]["title"]), "{{HERO_DATE}}": e(d["hero"]["date"]), "{{HERO_TAG}}": e(d["hero"]["tag"]),
            "{{HERO_STATS}}": stats, "{{HERO_TEXT}}": e(d["hero"]["text"]), "{{OPEN}}": openq, "{{FOOTER}}": e(d["footer"])}
    for k, v in fill.items():
        page = page.replace(k, v)
    # the JSON goes into a script element: "</" must not end it
    return page.replace("__DATA__", json.dumps(d, ensure_ascii=False).replace("</", "<" + chr(92) + "/"))


def render_md(d):
    page = ["**Page version:** " + d["page_url"], ""] if d.get("page_url") else []
    out = ["# " + d["title"], "", "_" + d["subtitle"] + "_", ""] + page + [ d["lead"], "", "> **Note:** " + d["note"], "",
           "## " + d["hero"]["title"], "", "**%s** — %s" % (d["hero"]["date"], d["hero"]["tag"]), "",
           " · ".join("%s: **%s**" % (k, v) for k, v in d["hero"]["stats"]), "", d["hero"]["text"], "", "## Timeline", ""]
    for p in d["phases"]:
        out += ["### %s — %s (%s)" % (p["t"], STATE[p["s"]], p["when"]), "",
                "%s · %s · %d/%d milestones, %d%%" % (p["v"], p["d"], p["m"][0], p["m"][1], percent(p["m"])), ""]
        if p.get("next"):
            out += ["**%s**" % p["next"], ""]
        out += ["- %s %s" % (MARK[k], t) for k, t in p["items"]] + [""]
    out += ["## Open decisions", ""] + ["- " + x for x in d["open"]] + ["", "---", d["footer"].replace("scripts/make_roadmap.py", "`scripts/make_roadmap.py`").replace("docs/roadmap.json", "`docs/roadmap.json`"), ""]
    return "\n".join(out)


def readme_blocks(d):
    """The two generated blocks of README.md: <!-- stats:start/end --> (the figures table) and <!-- status:start/end --> (where we are)."""
    stats = dict(d["hero"]["stats"])
    r = d["readme"]
    table = ["| Version | Missions completed | Executors | Models in use |", "|:---:|:---:|:---:|:---:|",
             "| **%s** | **%s** | %s | %s |" % (stats["Current version"], stats["Missions completed"], r["executors"], r["models"])]
    status = ["| | |", "|---|---|"] + ["| %s | %s |" % (a, b) for a, b in r["status"]]
    return {"stats": NL.join(table), "status": NL.join(status)}


def render_readme(d):
    """README.md with the generated blocks replaced; None when the file has no markers yet."""
    if not os.path.exists(README):
        return None
    text = open(README, encoding="utf-8", newline="").read()
    nl = CRLF if CRLF in text else NL
    for name, body in readme_blocks(d).items():
        start, end = "<!-- %s:start -->" % name, "<!-- %s:end -->" % name
        a, b = text.find(start), text.find(end)
        if a < 0 or b < a:
            return None
        text = text[:a + len(start)] + nl + body.replace(NL, nl) + nl + text[b:]
    return text


def counts_from_queue():
    """(completed, still in the queue) of the missions of THIS repository: the queue in data-tables/ops/real_queue.sh, filtered by the working
    directory of the mission (other projects such as JSA or the llama.cpp fork are not counted), and the Live State table.
    None when the ops folder is not there."""
    ops = os.path.join(ROOT, "data-tables", "ops")
    if not os.path.exists(os.path.join(ops, "pgdb.py")):
        print("--from-queue needs data-tables/ops (operator machine): the numbers stay as they are in docs/roadmap.json")
        return None
    import re
    sys.path.insert(0, ops)
    from pgdb import q
    text = open(os.path.join(ops, "real_queue.sh"), encoding="utf-8").read().replace(CRLF, NL)
    queue = re.search(r'^QUEUE="([^"]*)"', text, re.M).group(1).split()
    here = os.path.normcase(os.path.normpath(ROOT))
    mine = {r["mission_id"] for r in q('select mission_id, working_directory from "data_table_user_hYAqO52ZGHXxhhHX"')
            if os.path.normcase(os.path.normpath(str(r["working_directory"]).replace("/", os.sep))) == here}
    done = {r["state_key"] for r in q('select state_key from "data_table_user_Yn9lOhVhQ9gFymNs" where status = %s', ["completed"])}
    own = [m for m in queue if m in mine]
    left = [m for m in own if m not in done]
    return len(own) - len(left), len(left)   # completed, still in the queue (running one included)


def main():
    d = json.load(open(DATA, encoding="utf-8"))
    if "--from-queue" in sys.argv:
        counts = counts_from_queue()
        if counts:
            for pair in d["hero"]["stats"]:
                if pair[0] == "Missions completed":
                    pair[1] = str(counts[0])
                if pair[0] == "Missions in the queue":
                    pair[1] = str(counts[1])
            with open(DATA, "w", encoding="utf-8", newline=NL) as handle:
                handle.write(json.dumps(d, ensure_ascii=False, indent=2) + NL)
            print("queue numbers: %d completed, %d in the queue" % counts)
    files = {OUT_HTML: render_html(d), OUT_MD: render_md(d)}
    readme = render_readme(d)
    if readme is not None:
        files[README] = readme
    if "--check" in sys.argv:
        stale = [os.path.relpath(p, ROOT) for p, text in files.items()
                 if not os.path.exists(p) or open(p, encoding="utf-8", newline="").read() != text]
        print("up to date" if not stale else "out of date: " + ", ".join(stale))
        sys.exit(1 if stale else 0)
    for path, text in files.items():
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print("wrote", os.path.relpath(path, ROOT))


if __name__ == "__main__":
    main()
