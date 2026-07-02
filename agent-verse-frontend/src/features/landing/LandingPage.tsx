/**
 * AgentVerse — World-class marketing landing page.
 *
 * Design direction: Dark cosmic intelligence.
 * - Midnight + deep violet background with noise grain overlay
 * - Electric violet / cyan accent glow system
 * - Syne (display) + Inter (body) typography
 * - CSS-only scroll-reveal via IntersectionObserver
 * - Glassmorphism cards, animated grid, live terminal mockup
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

// ── Helpers ──────────────────────────────────────────────────────────────────

function useScrollReveal(threshold = 0.15) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) { setVisible(true); obs.disconnect(); } },
      { threshold }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [threshold]);

  return { ref, visible };
}

function useCounter(target: number, duration = 2000, started = false) {
  const [val, setVal] = useState(0);
  useEffect(() => {
    if (!started) return;
    let frame: number;
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      setVal(Math.floor(eased * target));
      if (p < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, duration, started]);
  return val;
}

// ── Terminal demo lines ───────────────────────────────────────────────────────

const DEMO_LINES = [
  { t: 0,    text: "$ agent submit --goal \"Find all Jira tickets assigned to Abhay\"", kind: "cmd" },
  { t: 600,  text: "✓ Goal accepted  [id: f97bd161]", kind: "ok" },
  { t: 1100, text: "→ Planning...  [Planner: GPT-4o]", kind: "dim" },
  { t: 1800, text: "→ Step 1: Search Jira with JQL — assignee = \"Abhay Dwivedi\"", kind: "step" },
  { t: 2600, text: "  ↳ Tool: jira_search_issues  [connector: PineLabs JIRA]", kind: "tool" },
  { t: 3400, text: "  ✓ Found 47 issues across 6 projects", kind: "ok" },
  { t: 4100, text: "→ Step 2: Verify result completeness", kind: "step" },
  { t: 4700, text: "  ✓ Verification passed — confidence: 0.97", kind: "ok" },
  { t: 5200, text: "✓ Goal complete  [2 steps · 0.7s · $0.0014]", kind: "success" },
  { t: 5900, text: "→ Result artifact saved. Run `agent results f97bd161` to view.", kind: "dim" },
];

const LINE_COLORS: Record<string, string> = {
  cmd:     "text-violet-300",
  ok:      "text-emerald-400",
  dim:     "text-slate-500",
  step:    "text-sky-300",
  tool:    "text-amber-300",
  success: "text-emerald-300 font-semibold",
};

function TerminalDemo() {
  const { ref, visible } = useScrollReveal(0.2);
  const [lines, setLines] = useState<typeof DEMO_LINES>([]);
  const [cursor, setCursor] = useState(true);

  useEffect(() => {
    if (!visible) return;
    const timers = DEMO_LINES.map(({ t }, i) =>
      setTimeout(() => setLines((prev) => [...prev, DEMO_LINES[i]]), t)
    );
    const blink = setInterval(() => setCursor((c) => !c), 530);
    return () => { timers.forEach(clearTimeout); clearInterval(blink); };
  }, [visible]);

  return (
    <div ref={ref} className={`landing-reveal ${visible ? "landing-reveal--visible" : ""}`}>
      <div className="relative mx-auto max-w-3xl">
        {/* Glow halo */}
        <div className="absolute -inset-6 bg-violet-600/10 blur-3xl rounded-3xl pointer-events-none" />
        <div className="relative bg-[#0d0d14] border border-white/[0.08] rounded-2xl overflow-hidden shadow-2xl">
          {/* Traffic lights */}
          <div className="flex items-center gap-1.5 px-4 py-3 border-b border-white/[0.06] bg-white/[0.02]">
            <span className="h-3 w-3 rounded-full bg-red-500/70" />
            <span className="h-3 w-3 rounded-full bg-amber-400/70" />
            <span className="h-3 w-3 rounded-full bg-emerald-400/70" />
            <span className="ml-3 text-xs text-slate-500 font-mono">agentverse — terminal</span>
          </div>
          {/* Lines */}
          <div className="px-5 py-5 font-mono text-sm leading-6 min-h-[280px]">
            {lines.map((ln, i) => (
              <div
                key={i}
                className={`${LINE_COLORS[ln.kind] ?? "text-slate-300"} terminal-line`}
                style={{ animationDelay: `${i * 20}ms` }}
              >
                {ln.text}
              </div>
            ))}
            {lines.length < DEMO_LINES.length && (
              <span className={`inline-block w-2 h-4 bg-violet-400 align-middle ${cursor ? "opacity-100" : "opacity-0"} transition-opacity`} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Feature card ─────────────────────────────────────────────────────────────

const FEATURES = [
  {
    icon: "⚡",
    title: "Zero-Code Autonomy",
    desc: "Submit a natural language goal. AgentVerse plans, executes, verifies — and replans on failure — with no hardcoded workflows.",
    glow: "from-violet-600/20",
  },
  {
    icon: "🔌",
    title: "227 Connectors",
    desc: "Jira, GitHub, Slack, Salesforce, HubSpot and 222 more. One registry. Real tool calls. Certified read/write parity.",
    glow: "from-sky-600/20",
  },
  {
    icon: "🛡️",
    title: "HITL Governance",
    desc: "High-risk actions pause for human approval. Audit trail, RBAC, cost budgets, PII guardrails — enterprise-grade by default.",
    glow: "from-emerald-600/20",
  },
  {
    icon: "🔭",
    title: "Real-Time Observability",
    desc: "Live SSE execution stream, DNA graph visualisation, diff-runs, ghost runs, and per-goal scoring.",
    glow: "from-amber-600/20",
  },
  {
    icon: "🤖",
    title: "Multi-Agent Patterns",
    desc: "Supervisor/debate orchestration, goal-tree decomposition, A2A task delegation across replicas.",
    glow: "from-pink-600/20",
  },
  {
    icon: "📈",
    title: "Self-Optimisation",
    desc: "Agents analyse their own failures and propose prompt improvements. EvalRunner scores every run across 5 dimensions.",
    glow: "from-cyan-600/20",
  },
];

function FeatureCard({ icon, title, desc, glow, delay }: (typeof FEATURES)[0] & { delay: number }) {
  const { ref, visible } = useScrollReveal(0.1);
  return (
    <div
      ref={ref}
      className={`landing-reveal ${visible ? "landing-reveal--visible" : ""} group relative overflow-hidden bg-white/[0.03] border border-white/[0.07] rounded-2xl p-6 hover:border-violet-500/30 hover:bg-white/[0.05] transition-all duration-300 cursor-default`}
      style={{ transitionDelay: `${delay}ms`, animationDelay: `${delay}ms` }}
    >
      <div className={`absolute inset-0 bg-gradient-to-br ${glow} to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none`} />
      <div className="text-3xl mb-4">{icon}</div>
      <h3 className="text-base font-semibold text-white mb-2">{title}</h3>
      <p className="text-sm text-slate-400 leading-relaxed">{desc}</p>
    </div>
  );
}

// ── Connector logos ───────────────────────────────────────────────────────────

const CONNECTORS = [
  { name: "Jira", color: "#2684FF" },
  { name: "GitHub", color: "#f0f6fc" },
  { name: "Slack", color: "#E01E5A" },
  { name: "Salesforce", color: "#00A1E0" },
  { name: "HubSpot", color: "#FF7A59" },
  { name: "Linear", color: "#5E6AD2" },
  { name: "Notion", color: "#ffffff" },
  { name: "Confluence", color: "#2684FF" },
  { name: "Asana", color: "#F06A6A" },
  { name: "Trello", color: "#0052CC" },
  { name: "Datadog", color: "#632CA6" },
  { name: "PagerDuty", color: "#06AC38" },
  { name: "GitLab", color: "#FC6D26" },
  { name: "Bitbucket", color: "#2684FF" },
  { name: "Zendesk", color: "#03363D" },
  { name: "Stripe", color: "#635BFF" },
];

// ── Stats ─────────────────────────────────────────────────────────────────────

function StatCounter({ value, suffix, label }: { value: number; suffix: string; label: string }) {
  const { ref, visible } = useScrollReveal(0.2);
  const count = useCounter(value, 2200, visible);
  return (
    <div ref={ref} className="text-center">
      <div className="text-4xl md:text-5xl font-bold text-white font-display tabular-nums">
        {count.toLocaleString()}{suffix}
      </div>
      <div className="text-sm text-slate-400 mt-2">{label}</div>
    </div>
  );
}

// ── How it works ──────────────────────────────────────────────────────────────

const HOW_STEPS = [
  { num: "01", title: "Submit a goal", desc: "Type any natural language objective — search Jira, draft a PR, analyse metrics, send alerts." },
  { num: "02", title: "Watch it plan & execute", desc: "The planner decomposes your goal into steps, the executor calls real tools, the verifier confirms success." },
  { num: "03", title: "Get structured results", desc: "A result artifact surfaces the outcome with evidence, supporting data and a shareable export." },
];

// ── Main component ────────────────────────────────────────────────────────────

export function LandingPage() {
  const navigate = useNavigate();
  const [words] = useState(["Search Jira.", "Write code.", "Analyse metrics.", "Trigger alerts.", "Draft emails.", "Query databases."]);
  const [wordIdx, setWordIdx] = useState(0);
  const [displayed, setDisplayed] = useState("");
  const [typing, setTyping] = useState(true);

  // Typewriter
  useEffect(() => {
    const word = words[wordIdx];
    if (typing) {
      if (displayed.length < word.length) {
        const t = setTimeout(() => setDisplayed(word.slice(0, displayed.length + 1)), 70);
        return () => clearTimeout(t);
      }
      const t = setTimeout(() => setTyping(false), 1600);
      return () => clearTimeout(t);
    } else {
      if (displayed.length > 0) {
        const t = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 40);
        return () => clearTimeout(t);
      }
      setWordIdx((i) => (i + 1) % words.length);
      setTyping(true);
    }
  }, [displayed, typing, wordIdx, words]);

  return (
    <div className="min-h-screen bg-[#080810] text-white overflow-x-hidden">
      {/* ── Background layer ────────────────────────────────────────────── */}
      <div className="fixed inset-0 pointer-events-none z-0">
        {/* Dot grid */}
        <div className="absolute inset-0 landing-grid-bg opacity-30" />
        {/* Gradient orbs */}
        <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-violet-800/20 rounded-full blur-[120px] animate-orb-drift" />
        <div className="absolute top-1/3 right-1/4 w-[400px] h-[400px] bg-sky-800/15 rounded-full blur-[100px] animate-orb-drift-slow" />
        <div className="absolute bottom-0 left-1/2 w-[500px] h-[300px] bg-indigo-900/20 rounded-full blur-[80px]" />
        {/* Noise grain */}
        <div className="absolute inset-0 landing-noise opacity-[0.03]" />
      </div>

      {/* ── Nav ──────────────────────────────────────────────────────────── */}
      <nav className="relative z-10 flex items-center justify-between px-6 md:px-12 py-5 border-b border-white/[0.05] backdrop-blur-sm bg-[#080810]/60">
        <div className="flex items-center gap-2.5">
          <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center">
            <span className="text-white text-sm font-bold">AV</span>
          </div>
          <span className="font-display font-semibold text-white text-lg tracking-tight">AgentVerse</span>
        </div>
        <div className="hidden md:flex items-center gap-8 text-sm text-slate-400">
          {["Features", "How it works", "Connectors", "Docs"].map((item) => (
            <a key={item} href={`#${item.toLowerCase().replace(/ /g, "-")}`}
              className="hover:text-white transition-colors duration-200">{item}</a>
          ))}
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate("/auth")}
            className="px-4 py-2 text-sm text-slate-300 hover:text-white transition-colors"
          >
            Sign in
          </button>
          <button
            onClick={() => navigate("/auth")}
            className="px-4 py-2 text-sm font-medium bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition-colors duration-200 shadow-lg shadow-violet-900/30"
          >
            Get started
          </button>
        </div>
      </nav>

      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <section className="relative z-10 pt-28 pb-20 px-6 md:px-12 text-center max-w-5xl mx-auto">
        {/* Badge */}
        <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-violet-500/30 bg-violet-500/10 text-violet-300 text-xs font-medium mb-8 animate-fade-in">
          <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-pulse" />
          <span>Autonomous AI agents — 227 connectors — enterprise-grade</span>
        </div>

        {/* Headline */}
        <h1 className="font-display text-5xl md:text-7xl font-bold text-white leading-[1.08] tracking-tight mb-6 animate-fade-in-up" style={{ animationDelay: "100ms" }}>
          One goal.<br />
          <span className="bg-gradient-to-r from-violet-400 via-indigo-400 to-sky-400 bg-clip-text text-transparent">
            Infinite execution.
          </span>
        </h1>

        {/* Typewriter sub */}
        <p className="text-lg md:text-2xl text-slate-400 mb-3 animate-fade-in-up" style={{ animationDelay: "200ms" }}>
          Tell your agent to
        </p>
        <div className="text-2xl md:text-3xl font-semibold text-white min-h-[2.5rem] mb-8 animate-fade-in-up" style={{ animationDelay: "260ms" }}>
          <span className="text-violet-300">{displayed}</span>
          <span className="inline-block w-0.5 h-7 bg-violet-400 ml-0.5 align-middle animate-blink" />
        </div>

        <p className="text-slate-400 text-base md:text-lg max-w-2xl mx-auto mb-10 leading-relaxed animate-fade-in-up" style={{ animationDelay: "340ms" }}>
          AgentVerse is a vendor-agnostic multi-tenant operating system for autonomous AI agents.
          Submit a natural language goal — the agent plans, calls real tools, verifies results
          and replans on failure.{" "}
          <span className="text-white font-medium">Zero hardcoded workflows.</span>
        </p>

        {/* CTAs */}
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4 animate-fade-in-up" style={{ animationDelay: "420ms" }}>
          <button
            onClick={() => navigate("/auth")}
            className="group relative px-7 py-3.5 font-semibold text-white bg-gradient-to-r from-violet-600 to-indigo-600 rounded-xl hover:shadow-xl hover:shadow-violet-900/40 transition-all duration-300 hover:scale-[1.02] active:scale-[0.98]"
          >
            <span className="absolute inset-0 rounded-xl bg-white/10 opacity-0 group-hover:opacity-100 transition-opacity" />
            Launch your first agent →
          </button>
          <a href="#how-it-works"
            className="px-7 py-3.5 font-medium text-slate-300 border border-white/[0.1] rounded-xl hover:border-white/20 hover:text-white transition-all duration-200 hover:bg-white/[0.03]">
            See how it works
          </a>
        </div>

        {/* Hero stats */}
        <div className="flex flex-wrap justify-center gap-8 mt-16 animate-fade-in-up" style={{ animationDelay: "520ms" }}>
          {[
            { val: "227", label: "connectors" },
            { val: "1905+", label: "E2E tests" },
            { val: "<1s", label: "avg plan time" },
            { val: "0", label: "hardcoded workflows" },
          ].map(({ val, label }) => (
            <div key={label} className="flex flex-col items-center">
              <span className="text-2xl font-bold text-white font-display">{val}</span>
              <span className="text-xs text-slate-500 mt-0.5">{label}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ── Terminal Demo ─────────────────────────────────────────────────── */}
      <section id="features" className="relative z-10 py-16 px-6 md:px-12">
        <TerminalDemo />
      </section>

      {/* ── Features grid ────────────────────────────────────────────────── */}
      <section className="relative z-10 py-20 px-6 md:px-12 max-w-6xl mx-auto">
        <div className="text-center mb-14">
          <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-4">
            Everything your agents need
          </h2>
          <p className="text-slate-400 max-w-xl mx-auto">
            From planning to verification, governance to observability — the full stack for production-grade autonomous AI.
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {FEATURES.map((f, i) => (
            <FeatureCard key={f.title} {...f} delay={i * 80} />
          ))}
        </div>
      </section>

      {/* ── How it works ─────────────────────────────────────────────────── */}
      <section id="how-it-works" className="relative z-10 py-20 px-6 md:px-12 max-w-5xl mx-auto">
        <div className="text-center mb-14">
          <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-4">
            How it works
          </h2>
          <p className="text-slate-400">Three steps from idea to structured result.</p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 relative">
          {/* Connecting line on md+ */}
          <div className="hidden md:block absolute top-12 left-[calc(16.7%+2rem)] right-[calc(16.7%+2rem)] h-px bg-gradient-to-r from-violet-500/30 via-sky-500/30 to-violet-500/30" />
          {HOW_STEPS.map(({ num, title, desc }, i) => {
            const { ref, visible } = useScrollReveal(0.1);
            return (
              <div
                key={num}
                ref={ref}
                className={`landing-reveal ${visible ? "landing-reveal--visible" : ""} text-center`}
                style={{ transitionDelay: `${i * 150}ms` }}
              >
                <div className="mx-auto mb-5 w-12 h-12 rounded-xl bg-gradient-to-br from-violet-600 to-indigo-700 flex items-center justify-center text-white font-bold font-mono text-sm shadow-lg shadow-violet-900/40">
                  {num}
                </div>
                <h3 className="font-semibold text-white text-base mb-2">{title}</h3>
                <p className="text-sm text-slate-400 leading-relaxed">{desc}</p>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── Connectors ───────────────────────────────────────────────────── */}
      <section id="connectors" className="relative z-10 py-20 px-6 md:px-12 overflow-hidden">
        <div className="text-center mb-12">
          <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-4">
            Connects to everything
          </h2>
          <p className="text-slate-400">
            227 certified connectors. Real tool calls. Production-ready authentication.
          </p>
        </div>
        {/* Marquee row 1 */}
        <div className="relative overflow-hidden mb-4">
          <div className="flex gap-3 animate-marquee">
            {[...CONNECTORS, ...CONNECTORS].map((c, i) => (
              <div
                key={i}
                className="flex-shrink-0 px-4 py-2.5 rounded-lg border border-white/[0.07] bg-white/[0.03] flex items-center gap-2.5 hover:border-white/20 transition-colors"
              >
                <div className="h-2 w-2 rounded-full" style={{ backgroundColor: c.color }} />
                <span className="text-sm text-slate-300 whitespace-nowrap">{c.name}</span>
              </div>
            ))}
          </div>
          <div className="absolute left-0 top-0 bottom-0 w-16 bg-gradient-to-r from-[#080810] to-transparent pointer-events-none z-10" />
          <div className="absolute right-0 top-0 bottom-0 w-16 bg-gradient-to-l from-[#080810] to-transparent pointer-events-none z-10" />
        </div>
        {/* Marquee row 2 (reverse) */}
        <div className="relative overflow-hidden">
          <div className="flex gap-3 animate-marquee-reverse">
            {[...CONNECTORS.slice(8), ...CONNECTORS.slice(0, 8), ...CONNECTORS.slice(8), ...CONNECTORS.slice(0, 8)].map((c, i) => (
              <div
                key={i}
                className="flex-shrink-0 px-4 py-2.5 rounded-lg border border-white/[0.07] bg-white/[0.03] flex items-center gap-2.5 hover:border-white/20 transition-colors"
              >
                <div className="h-2 w-2 rounded-full" style={{ backgroundColor: c.color }} />
                <span className="text-sm text-slate-300 whitespace-nowrap">{c.name}</span>
              </div>
            ))}
          </div>
          <div className="absolute left-0 top-0 bottom-0 w-16 bg-gradient-to-r from-[#080810] to-transparent pointer-events-none z-10" />
          <div className="absolute right-0 top-0 bottom-0 w-16 bg-gradient-to-l from-[#080810] to-transparent pointer-events-none z-10" />
        </div>
      </section>

      {/* ── Stats ────────────────────────────────────────────────────────── */}
      <section className="relative z-10 py-20 px-6 md:px-12">
        <div className="max-w-4xl mx-auto">
          <div className="rounded-2xl border border-white/[0.07] bg-gradient-to-br from-violet-900/20 to-indigo-900/10 p-10 md:p-16">
            <div className="text-center mb-12">
              <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-3">
                Built for scale
              </h2>
              <p className="text-slate-400">Numbers from a production-hardened codebase.</p>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
              <StatCounter value={227} suffix="+" label="Production connectors" />
              <StatCounter value={1905} suffix="+" label="E2E tests" />
              <StatCounter value={44} suffix="" label="DB migrations" />
              <StatCounter value={15} suffix="+" label="Agent architectures" />
            </div>
          </div>
        </div>
      </section>

      {/* ── Architecture highlights ───────────────────────────────────────── */}
      <section className="relative z-10 py-20 px-6 md:px-12 max-w-6xl mx-auto">
        <div className="text-center mb-14">
          <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-4">
            Production architecture
          </h2>
          <p className="text-slate-400 max-w-xl mx-auto">
            LangGraph state machines, Celery queue routing, Redis pub/sub SSE, row-level security — the full stack.
          </p>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {[
            {
              title: "LangGraph Agent Loop",
              desc: "initialize → plan → execute → verify → (complete | replan | waiting_human). Three distinct LLM roles, each independently tunable.",
              icon: "🔄",
              badge: "Core",
              color: "violet",
            },
            {
              title: "Multi-Tenant Queue Routing",
              desc: "Celery workers route by plan tier: goals.free / starter / professional / enterprise. Enterprise tenants never share queues.",
              icon: "📨",
              badge: "Scaling",
              color: "sky",
            },
            {
              title: "Row-Level Security",
              desc: "PostgreSQL RLS with SET LOCAL app.tenant_id. Tenant isolation enforced at the database, not just the app layer.",
              icon: "🔐",
              badge: "Security",
              color: "emerald",
            },
            {
              title: "Real-Time SSE Events",
              desc: "Per-goal asyncio subscriber queues with replay-before-register ordering. No events dropped between stream open and live subscription.",
              icon: "📡",
              badge: "Realtime",
              color: "amber",
            },
          ].map(({ title, desc, icon, badge, color }, i) => {
            const { ref, visible } = useScrollReveal(0.1);
            const badgeColors: Record<string, string> = {
              violet: "bg-violet-500/10 text-violet-300 border-violet-500/20",
              sky: "bg-sky-500/10 text-sky-300 border-sky-500/20",
              emerald: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
              amber: "bg-amber-500/10 text-amber-300 border-amber-500/20",
            };
            return (
              <div
                key={title}
                ref={ref}
                className={`landing-reveal ${visible ? "landing-reveal--visible" : ""} group p-6 rounded-2xl border border-white/[0.07] bg-white/[0.02] hover:border-white/[0.12] hover:bg-white/[0.04] transition-all duration-300`}
                style={{ transitionDelay: `${i * 100}ms` }}
              >
                <div className="flex items-start gap-4">
                  <div className="text-2xl mt-0.5">{icon}</div>
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <h3 className="font-semibold text-white text-sm">{title}</h3>
                      <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${badgeColors[color]}`}>{badge}</span>
                    </div>
                    <p className="text-sm text-slate-400 leading-relaxed">{desc}</p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── CTA Banner ───────────────────────────────────────────────────── */}
      <section className="relative z-10 py-24 px-6 md:px-12">
        <div className="max-w-3xl mx-auto text-center">
          {/* Glow */}
          <div className="absolute left-1/2 -translate-x-1/2 w-[500px] h-[200px] bg-violet-700/20 blur-[80px] rounded-full pointer-events-none" />
          <div className="relative">
            <h2 className="font-display text-4xl md:text-5xl font-bold text-white mb-6 leading-tight">
              Your first autonomous agent
              <br />
              <span className="bg-gradient-to-r from-violet-400 to-sky-400 bg-clip-text text-transparent">
                starts here.
              </span>
            </h2>
            <p className="text-slate-400 text-lg mb-10 max-w-lg mx-auto">
              Set up in minutes. No workflow builder. No drag and drop. Just a goal.
            </p>
            <div className="flex flex-col sm:flex-row gap-4 justify-center">
              <button
                onClick={() => navigate("/auth")}
                className="group relative px-8 py-4 font-semibold text-white bg-gradient-to-r from-violet-600 to-indigo-600 rounded-xl hover:shadow-2xl hover:shadow-violet-900/50 transition-all duration-300 hover:scale-[1.02] active:scale-[0.98] text-base"
              >
                <span className="absolute inset-0 rounded-xl bg-white/10 opacity-0 group-hover:opacity-100 transition-opacity" />
                Start for free →
              </button>
              <button
                onClick={() => navigate("/auth")}
                className="px-8 py-4 font-medium text-slate-300 border border-white/[0.1] rounded-xl hover:border-white/25 hover:text-white transition-all duration-200 text-base hover:bg-white/[0.03]"
              >
                View dashboard
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ───────────────────────────────────────────────────────── */}
      <footer className="relative z-10 border-t border-white/[0.05] py-12 px-6 md:px-12">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-start justify-between gap-8">
          {/* Brand */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <div className="h-6 w-6 rounded-md bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center">
                <span className="text-white text-[10px] font-bold">AV</span>
              </div>
              <span className="font-display font-semibold text-white">AgentVerse</span>
            </div>
            <p className="text-xs text-slate-500 max-w-xs leading-relaxed">
              A vendor-agnostic multi-tenant operating system for autonomous AI agents.
            </p>
          </div>
          {/* Links */}
          <div className="grid grid-cols-2 gap-8 text-sm">
            <div>
              <div className="text-white font-medium mb-3">Product</div>
              {["Features", "Connectors", "Pricing", "Changelog"].map((l) => (
                <div key={l} className="mb-1.5"><a href="#" className="text-slate-500 hover:text-slate-300 transition-colors">{l}</a></div>
              ))}
            </div>
            <div>
              <div className="text-white font-medium mb-3">Developers</div>
              {["API Reference", "Python SDK", "TypeScript SDK", "GitHub"].map((l) => (
                <div key={l} className="mb-1.5"><a href="#" className="text-slate-500 hover:text-slate-300 transition-colors">{l}</a></div>
              ))}
            </div>
          </div>
        </div>
        <div className="max-w-6xl mx-auto mt-10 pt-6 border-t border-white/[0.04] flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-slate-600">
          <span>© 2026 AgentVerse. Built for autonomous intelligence.</span>
          <span>React 19 · FastAPI · LangGraph · Celery · Postgres</span>
        </div>
      </footer>
    </div>
  );
}
