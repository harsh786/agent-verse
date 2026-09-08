#!/usr/bin/env python3
"""Assemble the World-Class Upgrade Plan — a data-driven roadmap page.
Scorecard and backlog are extracted from the manual's real ratings;
the workstreams and roadmap are hand-authored."""
from __future__ import annotations
import re, glob, os, html
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "assets" / "style.css").read_text()

CH = {
 "agent-patterns":"01 · Agent patterns","rag-patterns":"02 · RAG","agent-memory":"03 · Memory",
 "knowledge-and-graphs":"04 · Knowledge & graph","ingestion":"05 · Ingestion","retrieval-strategies":"06 · Retrieval",
 "embeddings":"07 · Embeddings","chunking":"17 · Chunking","prompt-builder":"08 · Prompt / context",
 "agent-improvement":"09 · Improvement","evals":"10 · Evals","hallucination":"18 · Hallucination",
 "guardrails":"12 · Guardrails","governance":"13 · Governance","scopes":"14 · Scopes","observability":"11 · Observability",
 "multimodal":"15 · Multimodal","ocr":"21 · OCR","model-router":"16 · Model router","platform-workflows":"19 · Core workflows",
 "platform-concepts":"20 · Platform concepts","agent-teams":"22 · Agent teams","jarvis":"23 · Jervis console",
 "coordination-protocols":"24 · Coordination","code-execution":"25 · Code execution","voice":"26 · Voice","triggers":"27 · Triggers",
 "reliability-qos":"28 · Reliability / QoS","cost-capacity":"29 · Cost / capacity","data-lifecycle":"30 · Data lifecycle",
 "testing-certification":"31 · Testing / cert","scale-playbook":"32 · Scale","workflow-engine":"34 · Workflow engine","frontier":"33 · Frontier"}

def load():
    rows=[]
    for f in glob.glob(str(ROOT/"parts"/"*.html")):
        slug=os.path.basename(f)[:-5]
        if slug not in CH: continue
        h=open(f).read()
        for m in re.finditer(r'<article class="concept"([^>]*)>\s*\n\s*<h3>(.*?)</h3>', h, re.S):
            a,t=m.group(1),re.sub(r'<[^>]+>','',m.group(2)).strip()
            im=re.search(r'data-impl="(\d+)"',a); cr=re.search(r'data-crit="(\d+)"',a)
            if im and cr: rows.append({"slug":slug,"ch":CH[slug],"t":t,"impl":int(im.group(1)),"crit":int(cr.group(1))})
    return rows

def bar(v, cls=""):
    return '<span class="pbar '+cls+'">'+''.join('<i class="on"></i>' if i<v else '<i></i>' for i in range(10))+'</span>'

def build():
    rows=load()
    n=len(rows); mi=sum(r["impl"] for r in rows)/n; mc=sum(r["crit"] for r in rows)/n
    shipped=sum(1 for r in rows if r["impl"]>=8); tens=sum(1 for r in rows if r["impl"]>=9)
    # priority backlog: weighted by criticality × gap
    for r in rows: r["gap"]=r["crit"]-r["impl"]; r["pri"]=r["gap"]*r["crit"]
    back=sorted([r for r in rows if r["gap"]>=2], key=lambda r:(-r["pri"],-r["crit"]))
    # chapter means
    byc={}
    for r in rows: byc.setdefault(r["ch"],[]).append(r)
    chmeans=sorted(((c, sum(x["impl"] for x in rs)/len(rs), sum(x["crit"] for x in rs)/len(rs), len(rs)) for c,rs in byc.items()), key=lambda x:x[1])

    backrows="".join(
        f'<tr><td class="c1">{html.escape(r["t"])}</td><td><span class="pill tag">{html.escape(r["ch"])}</span></td>'
        f'<td class="num">{r["impl"]}{bar(r["impl"])}</td><td class="num">→ 10</td>'
        f'<td class="num" style="color:var(--bad);font-weight:600">+{r["gap"]}</td></tr>' for r in back[:34])

    chrows="".join(
        f'<tr><td class="c1">{html.escape(c)}</td><td class="num">{im:.1f}{bar(round(im))}</td>'
        f'<td class="num">{cr:.1f}{bar(round(cr),"crit")}</td><td class="num">{k}</td></tr>'
        for c,im,cr,k in chmeans)

    css_extra = """
    .plan-hero{padding:52px 0 30px;border-bottom:2px solid var(--ink)}
    .plan-hero .cnum{background:var(--note-wash);color:var(--note);border-color:color-mix(in srgb,var(--note) 40%,transparent)}
    .pbar{display:inline-flex;gap:2px;vertical-align:middle;margin-left:8px}
    .pbar i{width:5px;height:10px;background:var(--line);border-radius:1px;display:block}
    .pbar i.on{background:var(--accent)}.pbar.crit i.on{background:var(--note)}
    .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:4px;overflow:hidden;margin:24px 0}
    .metrics div{background:var(--paper);padding:16px 18px}
    .metrics .k{font-family:var(--f-mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-4)}
    .metrics .v{font-family:var(--f-display);font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:4px}
    .metrics .v small{font-size:14px;color:var(--ink-3);font-weight:400}
    .flag{border:1px solid var(--accent-line);border-radius:5px;background:var(--accent-wash);padding:2px 20px 20px;margin:26px 0}
    .flag>h3{font-size:24px;font-weight:700;letter-spacing:-.02em;margin:20px 0 4px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
    .flag>h3 .tenten{font-family:var(--f-mono);font-size:12px;color:var(--accent);border:1px solid var(--accent-line);border-radius:3px;padding:3px 8px;letter-spacing:.05em}
    .ladder{display:grid;gap:0;margin:16px 0;border:1px solid var(--line);border-radius:4px;overflow:hidden}
    .ladder .rung{display:grid;grid-template-columns:56px 1fr;gap:0;border-top:1px solid var(--line-soft);background:var(--paper)}
    .ladder .rung:first-child{border-top:0}
    .ladder .lv{font-family:var(--f-mono);font-size:13px;font-weight:600;padding:12px 10px;text-align:center;color:var(--ink-3);background:var(--raised);display:flex;align-items:center;justify-content:center}
    .ladder .rung.now .lv{color:var(--warn);background:var(--warn-wash)}
    .ladder .rung.target .lv{color:var(--good);background:var(--good-wash)}
    .ladder .body{padding:11px 14px;font-size:15.5px}
    .ladder .body b{font-weight:650}
    .ladder .rung.now .body::after{content:" ← where it is today";font-family:var(--f-mono);font-size:11px;color:var(--warn);letter-spacing:.03em}
    .ladder .rung.target .body::after{content:" ← 10/10";font-family:var(--f-mono);font-size:11px;color:var(--good);letter-spacing:.03em}
    .phase{border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:0 5px 5px 0;padding:4px 20px 18px;margin:20px 0;background:var(--paper)}
    .phase.p2{border-left-color:var(--note)}.phase.p3{border-left-color:var(--good)}
    .phase>h3{font-size:20px;font-weight:700;margin:18px 0 4px}
    .phase .goal{font-family:var(--f-mono);font-size:11px;letter-spacing:.06em;color:var(--ink-3);text-transform:uppercase}
    .ws{border-top:1px solid var(--line);padding:22px 0 6px}
    .ws:first-of-type{border-top:2px solid var(--ink)}
    .ws>h3{font-size:20px;font-weight:700;letter-spacing:-.018em;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
    .ws>h3 .wsid{font-family:var(--f-mono);font-size:12px;color:var(--accent);border:1px solid var(--accent-line);border-radius:3px;padding:2px 7px}
    .dod{font-family:var(--f-mono);font-size:13px;background:var(--good-wash);border:1px solid color-mix(in srgb,var(--good) 30%,transparent);border-radius:4px;padding:10px 13px;margin:12px 0}
    .dod .lbl{color:var(--good);font-weight:600;letter-spacing:.08em;font-size:10px;text-transform:uppercase;display:block;margin-bottom:5px}
    """

    fonts=('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
     '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&'
     'family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap">')

    body = f"""<title>AgentVerse World-Class Plan</title>
<style>{CSS}{css_extra}</style>
{fonts}
<header class="topbar">
  <a class="brand" href="#top">AgentVerse <b>World-Class Plan</b></a>
  <span class="crumb">Roadmap to 10/10 · companion to the Field Manual</span>
  <span class="spacer"></span>
  <button class="tb-btn" data-theme-toggle type="button">Theme</button>
</header>
<main><div class="wrap" id="top">

<header class="plan-hero">
  <div class="kicker"><span class="cnum">UPGRADE PLAN</span><span class="eyebrow">From a complete platform to a world-class one</span></div>
  <h1 style="font-size:clamp(30px,4.4vw,46px);line-height:1.06;font-weight:700;letter-spacing:-.028em">Make every dimension 10/10</h1>
  <p class="lede">AgentVerse already implements the hard 80% of a production agent platform — 347 rated capabilities, mean implementation {mi:.1f}/10, {shipped} of them shipped. This plan is the remaining 20%: the specific, sequenced work to raise every dimension to 10/10, with <b>Harness Engineering</b> and <b>Context Engineering</b> — the two disciplines that decide whether agents are merely functional or genuinely world-class — taken to 10/10 first.</p>
  <div class="metrics">
    <div><div class="k">Rated capabilities</div><div class="v">{n}</div></div>
    <div><div class="k">Mean impl today</div><div class="v">{mi:.2f}<small>/10</small></div></div>
    <div><div class="k">Mean criticality</div><div class="v">{mc:.2f}<small>/10</small></div></div>
    <div><div class="k">Shipped (≥8)</div><div class="v">{shipped}<small>/{n}</small></div></div>
    <div><div class="k">At 9–10</div><div class="v">{tens}</div></div>
    <div><div class="k">Target</div><div class="v" style="color:var(--good)">10.0</div></div>
  </div>
</header>

<section class="block" id="method">
  <h2>The method</h2>
  <div class="prose">
    <p>"World-class" is not a vibe; it is a set of acceptance criteria. This plan defines, for every dimension,
    what 10/10 concretely means — a capability that is <b>shipped on the live path, governed, measured, learned,
    and provable</b> — and sequences the work to get there. Three principles shape it:</p>
    <ul>
      <li><b>Grounded in the real scorecard.</b> The priority backlog below is computed from the manual's own
      ratings: criticality × the gap to 10. Nothing here is aspirational hand-waving — it is the platform's
      actual code, ranked by where the distance to world-class is largest and matters most.</li>
      <li><b>Two flagships first.</b> Harness Engineering and Context Engineering are the load-bearing
      disciplines: every other capability runs on the harness and is fed by the context pipeline. Taking these
      two to 10/10 lifts the ceiling for everything else, so they lead.</li>
      <li><b>Quality-neutral before speculative.</b> The largest mass of work is nudging critical
      safety/reliability capabilities from 8 to 10 — hardening, calibration, coverage — which is low-risk and
      high-value. The speculative frontier (learned decisions, cross-tenant learning, formal guarantees) comes
      after the foundation is at 10.</li>
    </ul>
  </div>
  <div class="callout">
    <span class="lbl">What 10/10 means, precisely</span>
    <p>A capability is 10/10 when it is (1) on the live path for every relevant goal, (2) governed by policy and
    scope, (3) continuously measured by an eval with a threshold, (4) improved by a closed feedback loop rather
    than hand-tuning, and (5) explainable — its decisions reconstructable from the trace. Most of the platform
    meets 1–3 today; the jump to 10 is criteria 4 and 5.</p>
  </div>
</section>

<div class="flag">
<h3><span class="tenten">FLAGSHIP · target 10/10</span>Harness Engineering</h3>
<div class="prose">
<p>The <b>harness</b> is everything around the model call: the loop, the state machine, tool-call extraction and
validation, checkpointing, error taxonomy, cancellation, stall detection — the scaffolding that turns a
stochastic text generator into a reliable autonomous system. It is the platform's spine (chapters 1, 19, 28),
and it is strong today. World-class means removing the last sources of non-determinism, waste, and
hand-tuning.</p>
</div>
<h4 style="font-family:var(--f-mono);font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);margin:18px 0 8px">The capability ladder</h4>
<div class="ladder">
  <div class="rung"><div class="lv">6</div><div class="body">A <b>while-loop</b> around a model with basic tool calling and a retry.</div></div>
  <div class="rung now"><div class="lv">8</div><div class="body">A <b>checkpointed state machine</b> — bounded loops, stall detection on result digests, typed error taxonomy, cooperative cancellation, tool-call extract/repair/validate, durable resume across worker restarts. <i>AgentVerse today</i>: <code>graph.py</code>, <code>structured_executor.py</code>, <code>tool_calls.py</code>, <code>loop_engineering.py</code>.</div></div>
  <div class="rung target"><div class="lv">10</div><div class="body">All of 8, plus: <b>learned pattern selection</b> (a bandit over pattern sets, not a hand-written rule table); <b>speculative execution</b> of the high-confidence first step during planning; <b>plan caching</b> keyed by goal fingerprint so recurring goals skip re-planning; <b>a formal termination argument</b> for the highest-risk autonomous flows; and <b>self-tuning loop bounds</b> (iteration caps learned per goal class from measured outcomes).</div></div>
</div>
<h4 style="font-family:var(--f-mono);font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);margin:20px 0 8px">The four upgrades to 10</h4>
<div class="prose"><ol>
<li><b>Learned pattern assembly.</b> Replace the ordered rule table in <code>pattern_assembler.py</code> with a
contextual bandit trained on the eval/experiment signal (chapters 9–10), optimising measured success-per-dollar
per goal class — keeping the rule table as an auditable fallback and the append-only safety invariant intact.
<span class="dod"><span class="lbl">Done when</span> the assembler's choices beat the rule table on a held-out
goal set at equal-or-lower cost, every choice is recorded with its reason, and safety patterns remain
non-removable.</span></li>
<li><b>Speculative + cached execution.</b> Start executing the classifier's high-confidence first step while the
planner runs; cache validated plans by goal fingerprint so recurring goals (chapter 1) replan only on change.
<span class="dod"><span class="lbl">Done when</span> p50 time-to-first-action drops measurably on recurring
goal classes with no increase in wasted work, and a stale-plan guard re-validates preconditions on cache hit.</span></li>
<li><b>Deterministic resume, proven.</b> Close the remaining non-determinism in checkpoint/resume (pin clock and
randomness into state at first write) so a resumed run is byte-identical to the original, and add a replay test
(chapter 31) that asserts it.
<span class="dod"><span class="lbl">Done when</span> any goal can be replayed byte-identically from its
checkpoint, verified in CI.</span></li>
<li><b>Self-tuning bounds + termination argument.</b> Learn iteration caps per goal class from the measured
distribution; for money/data-mutating autonomous flows, add a checkable argument that the loop must terminate
(chapter 33).
<span class="dod"><span class="lbl">Done when</span> caps are data-derived per class and the highest-risk flows
carry a termination proof, not just a counter.</span></li>
</ol></div>
</div>

<div class="flag">
<h3><span class="tenten">FLAGSHIP · target 10/10</span>Context Engineering</h3>
<div class="prose">
<p><b>Context engineering</b> is the discipline of assembling exactly the right tokens into the prompt: retrieval
+ memory + tools + graph, reranked, capped per source, budgeted, cited, compressed, and rendered per role
(chapter 8). It is the single largest lever on both quality and cost — a model is only as good as what you put
in front of it. AgentVerse's context pipeline is genuinely strong (rated 8.1 mean); world-class means it packs
optimally, selects by learned value, and never regresses silently.</p>
</div>
<h4 style="font-family:var(--f-mono);font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);margin:18px 0 8px">The capability ladder</h4>
<div class="ladder">
  <div class="rung"><div class="lv">6</div><div class="body">Stuff retrieved chunks into a template until the window is full.</div></div>
  <div class="rung now"><div class="lv">8</div><div class="body">A <b>7-step budgeted pipeline</b> — rerank, per-source caps, token budget with floors, unified citation ids, one bundle rendered into per-role prompts, trust-layered (system/task/evidence/untrusted), with compression as a safety net. <i>AgentVerse today</i>: <code>context_pipeline.py</code>, <code>context_budget.py</code>, <code>rerank_policy.py</code>, <code>citation_manager.py</code>, <code>prompt_builder.py</code>.</div></div>
  <div class="rung target"><div class="lv">10</div><div class="body">All of 8, plus: <b>value-based packing</b> (an explicit value estimate per candidate item, solving the knapsack against the budget rather than filling greedily); <b>learned source-mix</b> (how much of the window each source earns, learned per goal class from answer quality); <b>prompt regression gates</b> (a context change cannot ship if it regresses a golden set); <b>per-section cost attribution</b> surfaced to tenants; and <b>cross-role consistency checks</b> (planner and verifier cannot drift into different definitions of success).</div></div>
</div>
<h4 style="font-family:var(--f-mono);font-size:11px;letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);margin:20px 0 8px">The five upgrades to 10</h4>
<div class="prose"><ol>
<li><b>Value-based context packing.</b> Attach a predicted value to each candidate (retrieval score × source
trust × recency × historical usefulness) and pack the budget to maximise total value, replacing greedy fill in
<code>context_budget.py</code>.
<span class="dod"><span class="lbl">Done when</span> answer quality at a fixed token budget beats the greedy
baseline on the eval set.</span></li>
<li><b>Learned source-mix.</b> Learn the per-source caps in <code>rerank_policy.py</code> per goal class from
measured outcomes instead of fixed percentages.
<span class="dod"><span class="lbl">Done when</span> the caps are data-derived and memory/tool/graph shares
adapt to what actually helps each goal class.</span></li>
<li><b>Prompt regression gates.</b> Wire the context pipeline into the regression gate (chapter 10): any change
to assembly, budget, or a prompt variant must pass a golden set before shipping.
<span class="dod"><span class="lbl">Done when</span> a context change that regresses grounding or success is
blocked in CI, with safety/grounding at zero tolerance.</span></li>
<li><b>Per-section cost attribution.</b> Surface the composition log (chapter 8) as a tenant-facing breakdown —
"tool schemas are 38% of your spend" — and drive the tool-schema-narrowing lever from it (chapter 29).
<span class="dod"><span class="lbl">Done when</span> every prompt's per-source token/cost split is queryable
and the biggest lever is visible per goal class.</span></li>
<li><b>Cross-role consistency.</b> Add a check that the planner's success criteria and the verifier's rubric
agree, so the two roles cannot silently diverge (chapter 8 gap).
<span class="dod"><span class="lbl">Done when</span> a drift between planner and verifier definitions of success
is detected and flagged automatically.</span></li>
</ol></div>
</div>

<section class="block" id="scorecard">
  <h2>The scorecard: where the platform stands</h2>
  <div class="prose"><p>Every chapter's mean implementation and criticality, from the manual's ratings. The
  pattern is the story: the core execution path and the safety/governance spine are already at 8+ (the platform
  is real), while the learned-intelligence frontier and a few specialised subsystems are where the climb to 10
  is longest. Read this top-down as the work queue.</p></div>
  <div class="tablewrap"><table class="matrix">
    <thead><tr><th>Chapter</th><th class="num">Mean impl</th><th class="num">Mean crit</th><th class="num">Concepts</th></tr></thead>
    <tbody>{chrows}</tbody></table></div>
</section>

<section class="block" id="backlog">
  <h2>The priority backlog</h2>
  <div class="prose"><p>The 34 highest-value upgrades, computed as <b>criticality × gap-to-10</b> — the
  capabilities where the distance to world-class is both largest and most consequential. Every one is already
  implemented (nothing here is below 6); the work is the last one-to-two points: calibration, coverage,
  learning, and proof. This is the definition of done for "everything at 10/10", ranked.</p></div>
  <div class="tablewrap"><table class="matrix">
    <thead><tr><th>Capability</th><th>Chapter</th><th class="num">Now</th><th class="num">Target</th><th class="num">Lift</th></tr></thead>
    <tbody>{backrows}</tbody></table></div>
</section>

<section class="block" id="workstreams">
  <h2>The nine workstreams</h2>
  <div class="prose"><p>Beyond the two flagships, the backlog groups into nine workstreams. Each names the
  capabilities it raises, what 10/10 looks like, and its definition of done.</p></div>

  <article class="ws"><h3><span class="wsid">W1</span>Learned intelligence replaces hand-tuned rules</h3>
  <div class="prose"><p>The single highest-leverage theme. Today the platform decides with ordered rule tables —
  pattern assembly (ch 1), RAG strategy selection (ch 2), model routing (ch 16), context caps (ch 8), retention
  (ch 30). Each is auditable and static. Replace each with a policy learned from the eval/experiment signal that
  already exists (ch 9–10), keeping rule-table fallbacks for auditability.</p>
  <div class="dod"><span class="lbl">Done when</span> pattern, RAG-strategy, model, and context-mix selection are
  each learned, beat their rule tables on held-out sets at equal-or-lower cost, and every decision is recorded
  with its reason and reversible to the rule-table fallback.</div></div></article>

  <article class="ws"><h3><span class="wsid">W2</span>Grounding &amp; anti-hallucination to 10</h3>
  <div class="prose"><p>Raises the highest-criticality cluster: retrieval confidence floors (ch 6, 18),
  deterministic + entailment grounding (ch 18), citation verification (ch 4). The last two points are
  <b>continuous per-collection threshold calibration</b> (not configured constants), <b>cross-source
  contradiction detection</b> (flag when two retrieved documents disagree), and <b>grounding as a hard output
  gate on every factual answer</b>.</p>
  <div class="dod"><span class="lbl">Done when</span> confidence floors are calibrated per collection from
  labelled feedback, contradictions are detected before synthesis, and no factual answer ships with an
  unsupported claim.</div></div></article>

  <article class="ws"><h3><span class="wsid">W3</span>Guardrails &amp; safety 8 → 10</h3>
  <div class="prose"><p>The guardrail spine (ch 12) is the platform's densest 8-rated, 10-critical cluster. To 10:
  <b>adaptive thresholds</b> tuned from the false-positive/negative signal simulate-mode already produces,
  <b>semantic injection detection</b> (payloads phrased as ordinary prose, not just patterns), <b>cross-tenant
  attack intelligence</b> (an attack seen once hardens everyone — see W8), and <b>guardrail latency budgeting</b>
  so output guards don't read as "the agent is slow".</p>
  <div class="dod"><span class="lbl">Done when</span> guardrail thresholds self-tune, semantic injection is
  caught, and the red-team corpus (ch 31) shows zero regressions with adaptive coverage.</div></div></article>

  <article class="ws"><h3><span class="wsid">W4</span>Reliability, QoS &amp; scale hardening</h3>
  <div class="prose"><p>Raises recovery/rollback (ch 1, 20, 28), idempotency, bulkheads, degraded mode, and the
  10M/day regime (ch 32). To 10: <b>adaptive concurrency limits</b> (learned from latency/error, not
  configured), <b>chaos/fault injection</b> to prove the primitives compose under failure, and the <b>scale last
  mile</b> — tenant sharding, cross-region active-active, and systematic p99 engineering.</p>
  <div class="dod"><span class="lbl">Done when</span> fault injection passes for every reliability primitive,
  bulkheads self-tune, and the 10M/day topology (sharding + multi-region + p99 targets) is built and
  load-tested.</div></div></article>

  <article class="ws"><h3><span class="wsid">W5</span>Data, provenance &amp; compliance completeness</h3>
  <div class="prose"><p>Raises classification, retention, deletion cascade, provenance (ch 30). To 10:
  <b>deletion verification</b> (an independent pass proving nothing survived the cascade), <b>classification
  accuracy monitoring</b> against human-corrected samples, <b>consent lineage</b> (each datum linked to the
  consent permitting its processing), and <b>continuous residency audit</b> (proof no datum crossed a region).</p>
  <div class="dod"><span class="lbl">Done when</span> a deletion produces a verifiable proof of completeness,
  classification accuracy is tracked, and residency is continuously audited.</div></div></article>

  <article class="ws"><h3><span class="wsid">W6</span>Multimodal &amp; voice deepening</h3>
  <div class="prose"><p>Raises multimodal (ch 15, mean 7.2) and voice (ch 26). To 10 where it earns it: <b>native
  joint embeddings</b> for visual search (ch 7), <b>cross-modal grounding</b> (verify a claim against a chart),
  <b>image-region chunking</b>, and <b>speech-to-speech</b> for the lowest-latency voice — each adopted only
  where extract-to-text demonstrably loses information, keeping extraction the governable default.</p>
  <div class="dod"><span class="lbl">Done when</span> visual search, cross-modal grounding, and low-latency voice
  are available where they beat extraction, without losing citability.</div></div></article>

  <article class="ws"><h3><span class="wsid">W7</span>Coordination &amp; teams maturity</h3>
  <div class="prose"><p>Raises coordination protocols (ch 24, mean 6.8) and agent teams (ch 22, 6.8) — the
  lowest-implemented non-frontier chapters. To 10: <b>protocol selection guidance</b> in the assembler (choosing
  among magentic/swarm/auction/etc. as deliberately as single-agent patterns), <b>team eval and certification</b>
  (measuring a formed team as a unit), <b>reputation grounded in eval scores</b> (ch 22), and <b>cross-protocol
  composition</b> as a first-class operation.</p>
  <div class="dod"><span class="lbl">Done when</span> coordination protocols are selected by measured fit, teams
  are certified as units, and reputation reflects eval outcomes.</div></div></article>

  <article class="ws"><h3><span class="wsid">W8</span>Privacy-preserving cross-tenant learning</h3>
  <div class="prose"><p>The largest unrealised value (ch 33, impl 3). Today every tenant learns alone — lessons,
  failure modes, and attack patterns are rediscovered per tenant. To 10: federated or differentially-private
  aggregation so an attack seen once hardens the whole fleet and a common failure mode is fixed once — with a
  <b>provable</b> privacy guarantee, starting with the safest signal (attack patterns, which are not tenant
  data).</p>
  <div class="dod"><span class="lbl">Done when</span> attack patterns and failure modes are shared fleet-wide with
  a demonstrated privacy guarantee and zero cross-tenant data exposure.</div></div></article>

  <article class="ws"><h3><span class="wsid">W9</span>Formal guarantees &amp; verifiable safety</h3>
  <div class="prose"><p>The longest-horizon frontier (ch 33, impl 3). For the highest-risk autonomous flows,
  move from empirical safety (controls + evals) to <b>checkable guarantees</b>: a proof that the loop must
  terminate (W-Harness), that the scope intersection cannot leak (ch 14), that a fail-closed control cannot
  degrade to fail-open. Start with the deterministic parts where proofs are tractable.</p>
  <div class="dod"><span class="lbl">Done when</span> loop termination and scope non-leakage carry checkable
  arguments for money/data-mutating flows, not just tests.</div></div></article>
</section>

<section class="block" id="roadmap">
  <h2>The phased roadmap</h2>
  <div class="prose"><p>Sequenced so the low-risk, high-value foundation is at 10 before the speculative frontier
  begins. Each phase is gated: a phase ships only when its definition of done passes the regression and safety
  gates (ch 10, 31).</p></div>

  <div class="phase p1">
    <span class="goal">Phase 1 · foundation to 10 · quality-neutral hardening</span>
    <h3>Take the critical 8s to 10</h3>
    <div class="prose"><p>The largest mass of work and the lowest risk: the two flagships (Harness + Context
    Engineering) and workstreams <b>W2, W3, W4-hardening, W5</b>. This is calibration, coverage, and
    regression-gating of capabilities that already work — grounding floors calibrated, guardrails adaptive,
    reliability fault-tested, deletion verified, context packed by value and regression-gated. Little of it
    risks quality; all of it raises the floor.</p>
    <p><b>Exit criteria:</b> every criticality-9+ capability at impl 9+, prompt/context changes regression-gated,
    red-team corpus green with adaptive coverage, deletion cascade provable.</p></div>
  </div>

  <div class="phase p2">
    <span class="goal">Phase 2 · learned intelligence · 9 → 10</span>
    <h3>Replace hand-tuned rules with learned decisions</h3>
    <div class="prose"><p>Workstreams <b>W1</b> (learned pattern/RAG/model/context selection) and <b>W7</b>
    (coordination/team maturity), plus the harness's learned bounds and speculative execution. This is where the
    platform stops being hand-tuned and starts optimising itself — wired to the eval/experiment machinery that
    already exists, with rule-table fallbacks kept for audit. Higher risk, higher reward; gated hard on the
    regression suite.</p>
    <p><b>Exit criteria:</b> learned selection beats rule tables on held-out sets at equal-or-lower cost, every
    decision explainable and reversible, coordination protocols selected by measured fit.</p></div>
  </div>

  <div class="phase p3">
    <span class="goal">Phase 3 · frontier · the 10/10 ceiling</span>
    <h3>Cross-tenant learning, scale last-mile, formal guarantees</h3>
    <div class="prose"><p>Workstreams <b>W8</b> (privacy-preserving cross-tenant learning), <b>W4-scale</b>
    (tenant sharding, multi-region, p99), <b>W6</b> (native multimodal where it wins), and <b>W9</b> (formal
    guarantees). These define the ceiling of trustworthy autonomy — the work that differentiates a world-class
    platform from a merely excellent one, gated on the maturity of the underlying techniques.</p>
    <p><b>Exit criteria:</b> fleet-wide learning with a proven privacy guarantee, 10M/day topology load-tested,
    and checkable safety arguments for the highest-risk flows.</p></div>
  </div>
</section>

<section class="block" id="close">
  <h2>Definition of world-class</h2>
  <div class="prose">
    <p>When this plan is complete, every one of the {n} capabilities meets all five criteria — live, governed,
    measured, learned, explainable — and the two flagship disciplines that decide agent quality are at 10/10:
    the <b>harness</b> that runs the agent is deterministic, self-tuning, and provably bounded; the <b>context
    engineering</b> that feeds it packs optimally, selects by learned value, and cannot silently regress.</p>
    <p>The honest sequence matters: the platform is already at {mi:.1f}/10 mean, so this is not a rebuild — it is
    the disciplined climb of the last two points, hardest and most valuable at the top. Phase 1 alone (the
    quality-neutral foundation) moves most of the mass; Phases 2 and 3 buy the self-improving intelligence and
    provable safety that separate world-class from complete.</p>
  </div>
  <p class="footnote">Companion to the AgentVerse Field Manual (36 chapters, {n} rated concepts). The backlog and
  scorecard are computed from the manual's ratings; the workstreams operationalise every chapter's "what is
  missing" section and the frontier chapter. Ratings are an informed reading of the code, not a benchmark —
  re-derive them as the code changes.</p>
</section>

</div></main>
<script>
(function(){{var r=document.documentElement;try{{var t=localStorage.getItem('avfm-theme');if(t)r.setAttribute('data-theme',t);}}catch(e){{}}
function cur(){{var a=r.getAttribute('data-theme');return a?a:(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');}}
document.addEventListener('click',function(ev){{var b=ev.target.closest('[data-theme-toggle]');if(!b)return;var n=cur()==='dark'?'light':'dark';r.setAttribute('data-theme',n);try{{localStorage.setItem('avfm-theme',n);}}catch(e){{}}}});}})();
</script>
"""
    (ROOT/"upgrade-plan.html").write_text(body)
    print(f"built upgrade-plan.html · {n} concepts · {len(back)} backlog items · mean impl {mi:.2f}")

if __name__=="__main__": build()
