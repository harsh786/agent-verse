#!/usr/bin/env python3
"""Assemble the AgentVerse Field Manual.

Sources
  parts/<slug>.html   chapter body (sections only; no chrome)
  assets/style.css    single stylesheet, inlined into every page

Outputs
  <slug>.html         standalone page (full document, shared chrome)
  index.html          hub + generated concept index
  appendix-ratings.html   generated ratings matrix
  bundle-artifact.html    single-file fragment for Artifact publishing
"""
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTS = ROOT / "parts"
CSS = (ROOT / "assets" / "style.css").read_text()

# part, num, slug, title, tagline
CHAPTERS: list[tuple[str, str, str, str, str]] = [
    ("I · The agent", "01", "agent-patterns", "Agent patterns",
     "Every control-flow shape an autonomous agent can take, from ReAct to LATS."),
    ("I · The agent", "02", "rag-patterns", "RAG patterns",
     "Fifteen retrieval architectures and the decision rule that picks one."),
    ("I · The agent", "03", "agent-memory", "Agent memories",
     "Seven memory tiers, their write paths, and what recall costs."),
    ("II · Knowledge", "04", "knowledge-and-graphs", "Knowledge & knowledge graph",
     "Collections, chunks, citations, and when a graph beats a vector."),
    ("II · Knowledge", "05", "ingestion", "Ingestion",
     "A 13-stage pipeline, 40+ connectors, and everything that breaks."),
    ("II · Knowledge", "06", "retrieval-strategies", "Retrieval strategies",
     "Vector, lexical, fusion, rerank — and how scores are made comparable."),
    ("II · Knowledge", "07", "embeddings", "Embeddings",
     "Routing, dimensions, drift, and the re-embedding migration nobody plans."),
    ("II · Knowledge", "17", "chunking", "Chunking strategies",
     "Twelve chunkers, the selector that dispatches them, and overlap math."),
    ("III · Context & quality", "08", "prompt-builder", "Prompt builder",
     "Context assembly as a budgeted, cited, role-specific compiler."),
    ("III · Context & quality", "09", "agent-improvement", "Agent improvement",
     "Closing the loop from eval signal to shipped prompt and model change."),
    ("III · Context & quality", "10", "evals", "Evals",
     "Offline suites, online scorecards, judges, and regression gates."),
    ("III · Context & quality", "18", "hallucination", "Hallucination handling",
     "Grounding, claim decomposition, NLI, calibration, fail-closed."),
    ("IV · Safety & control", "12", "guardrails", "Guardrails",
     "Injection, encoding, homoglyphs, PII, tool validation, fail-closed."),
    ("IV · Safety & control", "13", "governance", "Governance",
     "Tenancy, RBAC, RLS, policy engine, budgets, hash-chained audit."),
    ("IV · Safety & control", "14", "scopes", "Scopes",
     "Nine scope planes and the leak each one is there to prevent."),
    ("IV · Safety & control", "11", "observability", "Observability",
     "Logs, metrics, traces, decision traces, and the incident playbook."),
    ("V · Modality & models", "15", "multimodal", "Multimodal",
     "Text, PDF, image, screenshot, audio, video, tables, DOM."),
    ("V · Modality & models", "21", "ocr", "OCR",
     "Classify, extract, validate — document AI that has to be right."),
    ("V · Modality & models", "16", "model-router", "Multi-model router",
     "Per-role model selection under cost, latency, plan, and health."),
    ("VI · The platform", "19", "platform-workflows", "Core platform workflows",
     "Startup to SSE: the twenty-two steps a goal actually takes."),
    ("VI · The platform", "20", "platform-concepts", "Other core platform concepts",
     "MCP, providers, bulkheads, breakers, dedup, rollback, Redis, RLS."),
    ("VI · The platform", "22", "agent-teams", "AI agent teams",
     "Departments, roles, team formation, autonomy tiers, org learning."),
    ("VI · The platform", "23", "jarvis", "Jervis (JARVIS) console",
     "The operator cockpit: design tokens, live streams, human control."),
    ("VII · Not on the list", "24", "coordination-protocols", "Coordination protocols",
     "Magentic, group chat, swarm, auction, CAMEL, MoA, handoffs, A2A."),
    ("VII · Not on the list", "25", "code-execution", "Code execution & sandboxing",
     "Running model-written code without giving away the cluster."),
    ("VII · Not on the list", "26", "voice", "Voice & realtime agents",
     "STT, barge-in, intent routing, TTS, and the latency budget."),
    ("VII · Not on the list", "27", "triggers", "Triggers & event-driven agents",
     "Cron, webhooks, IoT, data change — agents nobody typed a prompt into."),
    ("VII · Not on the list", "28", "reliability-qos", "Reliability & QoS",
     "Bulkheads, breakers, idempotency, backpressure, degraded mode."),
    ("VII · Not on the list", "29", "cost-capacity", "Cost & capacity engineering",
     "Unit economics of an agent, and how to make them survive scale."),
    ("VII · Not on the list", "30", "data-lifecycle", "Data lifecycle & provenance",
     "Classification, residency, retention, legal hold, export, deletion."),
    ("VII · Not on the list", "31", "testing-certification", "Testing & certification",
     "Simulation, red team, golden datasets, readiness gates, replay."),
    ("VII · Not on the list", "32", "scale-playbook", "Scale playbook",
     "Deployment topology and the numbers at 10, 10k, and 10M goals/day."),
    ("VII · Not on the list", "34", "workflow-engine", "Workflow engine",
     "Deterministic workflows, chat, and collaboration alongside the agent loop."),
    ("VII · Not on the list", "33", "frontier", "Frontier & gaps",
     "What this architecture cannot yet do, and what to build next."),
]

APPENDICES: list[tuple[str, str, str, str, str]] = [
    ("Appendix", "A", "appendix-ratings", "Ratings matrix",
     "Every rated concept in one sortable table."),
    ("Appendix", "B", "appendix-glossary", "Glossary",
     "The vocabulary, defined once."),
]

ALL = CHAPTERS + APPENDICES

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Archivo:wght@400;500;600;700&'
         'family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&'
         'family=IBM+Plex+Mono:wght@400;500;600&display=swap">')

THEME_JS = """
(function () {
  var r = document.documentElement;
  try { var t = localStorage.getItem('avfm-theme'); if (t) r.setAttribute('data-theme', t); } catch (e) {}
  function cur() {
    var a = r.getAttribute('data-theme');
    if (a) return a;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-theme-toggle]');
    if (!b) return;
    var n = cur() === 'dark' ? 'light' : 'dark';
    r.setAttribute('data-theme', n);
    try { localStorage.setItem('avfm-theme', n); } catch (e) {}
  });
  var f = document.querySelector('[data-filter]');
  if (f) {
    f.addEventListener('input', function () {
      var q = f.value.trim().toLowerCase();
      document.querySelectorAll('[data-filter-row]').forEach(function (el) {
        el.hidden = q.length > 0 && el.getAttribute('data-filter-row').indexOf(q) === -1;
      });
    });
  }
})();
"""

BUNDLE_JS = """
(function () {
  var r = document.documentElement;
  try { var t = localStorage.getItem('avfm-theme'); if (t) r.setAttribute('data-theme', t); } catch (e) {}
  function cur() {
    var a = r.getAttribute('data-theme');
    if (a) return a;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  var chapters = Array.prototype.slice.call(document.querySelectorAll('[data-chapter]'));
  var links = Array.prototype.slice.call(document.querySelectorAll('[data-goto]'));
  function show(slug, push) {
    var found = false;
    chapters.forEach(function (c) {
      var on = c.getAttribute('data-chapter') === slug;
      c.hidden = !on;
      if (on) found = true;
    });
    if (!found) { return show('hub', push); }
    links.forEach(function (a) {
      if (a.getAttribute('data-goto') === slug) { a.setAttribute('aria-current', 'page'); }
      else { a.removeAttribute('aria-current'); }
    });
    var crumb = document.querySelector('[data-crumb]');
    var host = document.querySelector('[data-chapter="' + slug + '"]');
    if (crumb && host) { crumb.textContent = host.getAttribute('data-crumb') || ''; }
    if (push && window.history && history.replaceState) { history.replaceState(null, '', '#' + slug); }
    window.scrollTo(0, 0);
  }
  document.addEventListener('click', function (ev) {
    var tog = ev.target.closest('[data-theme-toggle]');
    if (tog) {
      var n = cur() === 'dark' ? 'light' : 'dark';
      r.setAttribute('data-theme', n);
      try { localStorage.setItem('avfm-theme', n); } catch (e) {}
      return;
    }
    var a = ev.target.closest('[data-goto]');
    if (a) {
      ev.preventDefault();
      show(a.getAttribute('data-goto'), true);
      var anchor = a.getAttribute('data-anchor');
      if (anchor) {
        var t = document.getElementById(anchor);
        if (t) { t.scrollIntoView({block: 'start'}); }
      }
    }
  });
  var start = (location.hash || '#hub').slice(1);
  show(chapters.some(function (c) { return c.getAttribute('data-chapter') === start; }) ? start : 'hub', false);
  var f = document.querySelector('[data-filter]');
  if (f) {
    f.addEventListener('input', function () {
      var q = f.value.trim().toLowerCase();
      document.querySelectorAll('[data-filter-row]').forEach(function (el) {
        el.hidden = q.length > 0 && el.getAttribute('data-filter-row').indexOf(q) === -1;
      });
    });
  }
})();
"""

CONCEPT_RE = re.compile(
    r'<article class="concept"([^>]*)>\s*\n\s*<h3>(.*?)</h3>', re.S)
ATTR_RE = re.compile(r'([a-zA-Z-]+)="([^"]*)"')


def meter(v: int, extra: str = "") -> str:
    cells = "".join('<i class="on"></i>' if i < v else "<i></i>" for i in range(10))
    return f'<span class="meter{extra}" aria-hidden="true">{cells}</span>'


def status_of(impl: int) -> tuple[str, str]:
    if impl >= 8:
        return "shipped", "Shipped"
    if impl >= 5:
        return "partial", "Partial"
    return "thin", "Thin"


def build_dials(attrs: dict[str, str]) -> str:
    impl = int(attrs.get("data-impl", "0") or 0)
    crit = int(attrs.get("data-crit", "0") or 0)
    cls, label = status_of(impl)
    tag = attrs.get("data-tag", "")
    out = ['<div class="dials">']
    out.append(f'<span class="pill {cls}">{html.escape(label)}</span>')
    if tag:
        out.append(f'<span class="pill tag">{html.escape(tag)}</span>')
    out.append('<span class="dial"><span class="dl">Impl</span>'
               f'{meter(impl)}<span class="dv">{impl}<small>/10</small></span></span>')
    out.append('<span class="dial crit"><span class="dl">Criticality</span>'
               f'{meter(crit)}<span class="dv">{crit}<small>/10</small></span></span>')
    out.append("</div>")
    return "".join(out)


def process_part(slug: str, body: str) -> tuple[str, list[dict]]:
    """Inject dials + anchors; harvest the concept list."""
    concepts: list[dict] = []
    counter = [0]

    def repl(m: re.Match) -> str:
        raw_attrs, title = m.group(1), m.group(2).strip()
        attrs = dict(ATTR_RE.findall(raw_attrs))
        cid = attrs.get("id", "")
        counter[0] += 1
        n = counter[0]
        impl = int(attrs.get("data-impl", "0") or 0)
        crit = int(attrs.get("data-crit", "0") or 0)
        plain = re.sub(r"<[^>]+>", "", title)
        concepts.append({
            "n": n, "id": cid, "title": title, "plain": plain,
            "impl": impl, "crit": crit, "tag": attrs.get("data-tag", ""),
            "slug": slug,
        })
        head = (f'<article class="concept"{raw_attrs}>\n'
                f'<h3><span class="idx">{n:02d}</span>{title}'
                f'<a class="anchor" href="#{cid}" aria-label="Link to this concept">#</a></h3>\n'
                f"{build_dials(attrs)}")
        return head

    return CONCEPT_RE.sub(repl, body), concepts


def rail(active: str, concepts: list[dict], link: str = "") -> str:
    """link='' -> standalone hrefs; link='bundle' -> data-goto switches."""
    def href(slug: str) -> str:
        return f'data-goto="{slug}" href="#{slug}"' if link == "bundle" else f'href="{slug}.html"'

    out = ['<aside class="rail">']
    out.append('<h4>Field manual</h4><nav>')
    hub = 'data-goto="hub" href="#hub"' if link == "bundle" else 'href="index.html"'
    cur = ' aria-current="page"' if active == "hub" else ""
    out.append(f'<a {hub}{cur}><span class="n">00</span><span>Hub &amp; how to read</span></a>')
    out.append("</nav>")
    part = None
    for p, num, slug, title, _tag in ALL:
        if p != part:
            part = p
            out.append(f'</nav><div class="part-label eyebrow">{html.escape(p)}</div><nav>')
        cur = ' aria-current="page"' if slug == active else ""
        out.append(f'<a {href(slug)}{cur}><span class="n">{num}</span><span>{html.escape(title)}</span></a>')
    out.append("</nav>")
    if concepts:
        out.append('<div class="inpage"><h4>In this chapter</h4><nav>')
        for c in concepts:
            out.append(f'<a href="#{c["id"]}"><span class="n">{c["n"]:02d}</span>'
                       f'<span>{html.escape(c["plain"])}</span></a>')
        out.append("</nav></div>")
    out.append("</aside>")
    return "".join(out)


def chapter_head(num: str, title: str, tag: str, lede: str, concepts: list[dict]) -> str:
    impl = [c["impl"] for c in concepts if c["impl"]]
    crit = [c["crit"] for c in concepts if c["crit"]]
    strip = ""
    if concepts:
        avg_i = sum(impl) / len(impl) if impl else 0
        avg_c = sum(crit) / len(crit) if crit else 0
        shipped = sum(1 for v in impl if v >= 8)
        gaps = sum(1 for v in impl if v <= 4)
        strip = (
            '<div class="statstrip">'
            f'<div><div class="k">Concepts</div><div class="v">{len(concepts)}</div></div>'
            f'<div><div class="k">Mean impl</div><div class="v">{avg_i:.1f}</div></div>'
            f'<div><div class="k">Mean criticality</div><div class="v">{avg_c:.1f}</div></div>'
            f'<div><div class="k">Shipped</div><div class="v">{shipped}</div></div>'
            f'<div><div class="k">Thin / missing</div><div class="v">{gaps}</div></div>'
            "</div>")
    return ('<header class="chead"><div class="kicker">'
            f'<span class="cnum">CH {num}</span><span class="eyebrow">{html.escape(tag)}</span>'
            f'</div><h1>{html.escape(title)}</h1>'
            f'<p class="lede">{lede}</p>{strip}</header>')


def pagenav(idx: int, link: str = "") -> str:
    def a(i: int, cls: str, dirn: str) -> str:
        _p, num, slug, title, _t = ALL[i]
        href = f'data-goto="{slug}" href="#{slug}"' if link == "bundle" else f'href="{slug}.html"'
        return (f'<a class="{cls}" {href}><div class="d">{dirn} · CH {num}</div>'
                f'<div class="t">{html.escape(title)}</div></a>')
    out = ['<div class="pagenav">']
    out.append(a(idx - 1, "prev", "Previous") if idx > 0 else
               ('<a class="prev" ' + ('data-goto="hub" href="#hub"' if link == "bundle" else 'href="index.html"')
                + '><div class="d">Back</div><div class="t">Hub</div></a>'))
    if idx < len(ALL) - 1:
        out.append(a(idx + 1, "next", "Next"))
    out.append("</div>")
    return "".join(out)


def page(title: str, crumb: str, body: str, active: str, concepts: list[dict], nav: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(crumb)}">
{FONTS}
<style>{CSS}</style>
</head>
<body>
<header class="topbar">
  <a class="brand" href="index.html">AgentVerse <b>Field Manual</b></a>
  <span class="crumb">{html.escape(crumb)}</span>
  <span class="spacer"></span>
  <button class="tb-btn" data-theme-toggle type="button">Theme</button>
</header>
<div class="shell">
{rail(active, concepts)}
<main><div class="wrap">
{body}
{nav}
<p class="footnote">AgentVerse Field Manual · ratings are this author's judgement of the code as it stands, not a vendor claim. File paths are navigation anchors; line numbers drift.</p>
</div></main>
</div>
<script>{THEME_JS}</script>
</body>
</html>
"""


def concept_index(harvest: dict[str, list[dict]], link: str = "") -> str:
    """The generated master index of every rated concept."""
    def chap_href(slug: str) -> str:
        if link == "bundle":
            return f'data-goto="{slug}" href="#{slug}"'
        return f'href="{slug}.html"'

    def concept_href(slug: str, cid: str) -> str:
        if link == "bundle":
            return f'data-goto="{slug}" data-anchor="{cid}" href="#{cid}"'
        return f'href="{slug}.html#{cid}"'

    def bar(v: int, cls: str = "") -> str:
        cells = "".join('<i class="on"></i>' if i < v else "<i></i>" for i in range(10))
        return f'<span class="bar {cls}">{cells}</span>'

    rows: list[str] = []
    total = 0
    for _p, num, slug, title, _t in ALL:
        cs = harvest.get(slug, [])
        if not cs:
            continue
        rows.append(
            f'<tr data-filter-row="{html.escape(title.lower())}"><td colspan="5" class="idxhead">'
            f'CH {num} · <a {chap_href(slug)}>{html.escape(title)}</a></td></tr>')
        for c in cs:
            total += 1
            cls, label = status_of(c["impl"])
            key = f'{c["plain"]} {title} {c["tag"]} {label}'.lower()
            rows.append(
                f'<tr data-filter-row="{html.escape(key)}">'
                f'<td class="c1"><a {concept_href(slug, c["id"])}>{html.escape(c["plain"])}</a></td>'
                f'<td><span class="pill tag">{html.escape(c["tag"])}</span></td>'
                f'<td class="num">{c["impl"]}{bar(c["impl"])}</td>'
                f'<td class="num">{c["crit"]}{bar(c["crit"], "crit")}</td>'
                f'<td><span class="pill {cls}">{label}</span></td></tr>')

    return (
        f'<section class="block" id="concept-index"><h2>Concept index — {total} rated concepts</h2>'
        '<div class="prose"><p>Every concept in the manual with both ratings. Filter by name, '
        'chapter, kind, or status.</p></div>'
        '<div style="margin:14px 0"><input data-filter type="search" '
        'placeholder="filter concepts…" aria-label="Filter concepts" '
        'style="font-family:var(--f-mono);font-size:12.5px;padding:8px 10px;width:min(360px,100%);'
        'background:var(--paper);color:var(--ink);border:1px solid var(--line);border-radius:3px"></div>'
        '<div class="tablewrap"><table class="matrix"><thead><tr>'
        '<th>Concept</th><th>Kind</th><th class="num">Impl</th><th class="num">Crit</th><th>Status</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>')


def main() -> None:
    harvest: dict[str, list[dict]] = {}
    bodies: dict[str, str] = {}
    for _p, _num, slug, _title, _t in ALL:
        f = PARTS / f"{slug}.html"
        if not f.exists():
            continue
        processed, concepts = process_part(slug, f.read_text())
        bodies[slug] = processed
        harvest[slug] = concepts

    # ---- standalone chapter pages
    written = []
    for i, (p, num, slug, title, tagline) in enumerate(ALL):
        if slug not in bodies:
            continue
        crumb = f"CH {num} · {title}"
        head = chapter_head(num, title, p, tagline, harvest[slug])
        body = head + bodies[slug]
        if slug == "appendix-ratings":
            body += concept_index(harvest)
        (ROOT / f"{slug}.html").write_text(
            page(f"{title} · AgentVerse Field Manual", crumb, body, slug, harvest[slug], pagenav(i)))
        written.append(slug)

    # ---- hub
    hub_part = PARTS / "_hub.html"
    if hub_part.exists():
        grid = ['<section class="block" id="chapters"><h2>The 35 chapters</h2><div class="chapgrid">']
        for p, num, slug, title, tagline in ALL:
            cs = harvest.get(slug, [])
            meta = f"{len(cs)} concepts" if cs else "in progress"
            grid.append(f'<a href="{slug}.html"><span class="n">CH {num} · {html.escape(p)}</span>'
                        f'<span class="t">{html.escape(title)}</span>'
                        f'<span class="d">{tagline}</span><span class="m">{meta}</span></a>')
        grid.append("</div></section>")
        body = hub_part.read_text() + "".join(grid) + concept_index(harvest)
        (ROOT / "index.html").write_text(
            page("AgentVerse Field Manual", "Hub · how to read this", body, "hub", [], ""))

    # ---- bundle (Artifact fragment: no doctype/html/head/body)
    parts_out = [f"<style>{CSS}</style>", FONTS,
                 '<header class="topbar"><a class="brand" data-goto="hub" href="#hub">'
                 'AgentVerse <b>Field Manual</b></a><span class="crumb" data-crumb></span>'
                 '<span class="spacer"></span>'
                 '<button class="tb-btn" data-theme-toggle type="button">Theme</button></header>',
                 '<div class="shell">', rail("hub", [], "bundle"), '<main><div class="wrap">']
    if hub_part.exists():
        grid = ['<section class="block" id="chapters"><h2>The 35 chapters</h2><div class="chapgrid">']
        for p, num, slug, title, tagline in ALL:
            cs = harvest.get(slug, [])
            meta = f"{len(cs)} concepts" if cs else "in progress"
            grid.append(f'<a data-goto="{slug}" href="#{slug}"><span class="n">CH {num} · {html.escape(p)}</span>'
                        f'<span class="t">{html.escape(title)}</span>'
                        f'<span class="d">{tagline}</span><span class="m">{meta}</span></a>')
        grid.append("</div></section>")
        parts_out.append('<div data-chapter="hub" data-crumb="Hub · how to read this">'
                         + hub_part.read_text() + "".join(grid)
                         + concept_index(harvest, "bundle") + "</div>")
    for i, (p, num, slug, title, tagline) in enumerate(ALL):
        if slug not in bodies:
            continue
        head = chapter_head(num, title, p, tagline, harvest[slug])
        extra = concept_index(harvest, "bundle") if slug == "appendix-ratings" else ""
        parts_out.append(f'<div data-chapter="{slug}" data-crumb="CH {num} · {html.escape(title)}" hidden>'
                         + head + bodies[slug] + extra + pagenav(i, "bundle") + "</div>")
    parts_out.append('</div></main></div>')
    parts_out.append(f"<script>{BUNDLE_JS}</script>")
    (ROOT / "bundle-artifact.html").write_text(
        "<title>AgentVerse Field Manual</title>\n" + "\n".join(parts_out))

    n_concepts = sum(len(v) for v in harvest.values())
    print(f"built {len(written)} chapter pages + hub + bundle · {n_concepts} concepts")
    missing = [s for _p, _n, s, _t, _g in ALL if s not in bodies]
    if missing:
        print("pending parts:", " ".join(missing))


if __name__ == "__main__":
    main()
