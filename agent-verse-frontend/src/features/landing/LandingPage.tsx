/**
 * AgentVerse Landing Page — Obsidian Intelligence
 *
 * Design: Dark-luxury editorial. Investor-grade. Every section earns attention.
 * Fonts:  Syne (display) + Inter (body)
 * Motion: CSS-only IntersectionObserver reveals, no external libs
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────

function useReveal(threshold = 0.12) {
  const ref = useRef<HTMLDivElement>(null);
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setOn(true); io.disconnect(); } },
      { threshold }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [threshold]);
  return { ref, on };
}

function useCounter(target: number, duration = 1800, active = false) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!active) return;
    let raf: number;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min((now - t0) / duration, 1);
      setN(Math.floor((1 - Math.pow(1 - p, 4)) * target));
      if (p < 1) raf = requestAnimationFrame(tick);
      else setN(target);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration, active]);
  return n;
}

// ─────────────────────────────────────────────────────────────────────────────
// Hero — Typewriter + animated grid
// ─────────────────────────────────────────────────────────────────────────────

const VERBS = [
  "searches Jira across all projects.",
  "reviews pull requests & files issues.",
  "monitors services & pages on-call teams.",
  "analyses pipeline failures & proposes fixes.",
  "onboards new hires end-to-end.",
  "generates weekly executive reports.",
  "triages support tickets at scale.",
  "closes CRM deals automatically.",
];

function Typewriter() {
  const [idx, setIdx] = useState(0);
  const [text, setText] = useState("");
  const [writing, setWriting] = useState(true);

  useEffect(() => {
    const word = VERBS[idx];
    if (writing) {
      if (text.length < word.length) {
        const t = setTimeout(() => setText(word.slice(0, text.length + 1)), 52);
        return () => clearTimeout(t);
      }
      const t = setTimeout(() => setWriting(false), 1800);
      return () => clearTimeout(t);
    } else {
      if (text.length > 0) {
        const t = setTimeout(() => setText(t => t.slice(0, -1)), 28);
        return () => clearTimeout(t);
      }
      setIdx(i => (i + 1) % VERBS.length);
      setWriting(true);
    }
  }, [text, writing, idx]);

  return (
    <span className="text-violet-300 font-medium">
      {text}
      <span className="inline-block w-[3px] h-[1.1em] bg-violet-400 align-middle ml-0.5 animate-blink" />
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Agent execution demo — animated step pipeline
// ─────────────────────────────────────────────────────────────────────────────

const PIPELINE_STEPS = [
  { id: "plan", label: "Plan", icon: "◈", color: "text-violet-400", detail: "LLM decomposes goal → ordered steps" },
  { id: "rag", label: "Memory recall", icon: "◎", color: "text-indigo-400", detail: "Past plans & failures injected" },
  { id: "exec", label: "Execute", icon: "▶", color: "text-sky-400", detail: "Tool calls via 227 connectors" },
  { id: "guard", label: "Guardrails", icon: "⬡", color: "text-amber-400", detail: "PII scan · policy check · HITL gate" },
  { id: "verify", label: "Verify", icon: "✓", color: "text-emerald-400", detail: "Verifier LLM confirms success" },
  { id: "replan", label: "Replan if needed", icon: "↺", color: "text-rose-400", detail: "Auto-recover on failure" },
];

function PipelineDemo() {
  const { ref, on } = useReveal(0.2);
  const [active, setActive] = useState(-1);

  useEffect(() => {
    if (!on) return;
    let i = 0;
    const t = setInterval(() => {
      setActive(i);
      i++;
      if (i >= PIPELINE_STEPS.length) { i = 0; }
    }, 900);
    return () => clearInterval(t);
  }, [on]);

  return (
    <div ref={ref} className={`reveal ${on ? "reveal-on" : ""}`}>
      <div className="relative mx-auto max-w-4xl">
        <div className="absolute -inset-px bg-gradient-to-r from-violet-600/0 via-violet-600/20 to-violet-600/0 rounded-2xl blur-xl" />
        <div className="relative bg-[#0c0c18] border border-white/[0.07] rounded-2xl overflow-hidden">
          {/* Header */}
          <div className="flex items-center gap-2 px-5 py-3.5 border-b border-white/[0.06] bg-white/[0.015]">
            <span className="h-2.5 w-2.5 rounded-full bg-rose-500/60" />
            <span className="h-2.5 w-2.5 rounded-full bg-amber-500/60" />
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-500/60" />
            <span className="ml-3 font-mono text-xs text-slate-500">agent execution pipeline</span>
            <span className="ml-auto flex items-center gap-1.5 text-xs text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              live
            </span>
          </div>
          {/* Goal */}
          <div className="px-5 pt-5 pb-3">
            <div className="flex items-center gap-2 mb-5">
              <span className="text-xs text-slate-500 font-mono">goal:</span>
              <span className="text-sm text-white font-medium">
                &quot;Find all Jira tickets assigned to Abhay Dwivedi across all projects&quot;
              </span>
            </div>
            {/* Steps */}
            <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
              {PIPELINE_STEPS.map((s, i) => (
                <div
                  key={s.id}
                  className={`relative flex flex-col items-center gap-2 p-3 rounded-xl border transition-all duration-500 ${
                    active === i
                      ? "border-white/20 bg-white/[0.06] scale-105"
                      : active > i
                      ? "border-emerald-500/20 bg-emerald-900/10"
                      : "border-white/[0.04] bg-white/[0.01] opacity-40"
                  }`}
                >
                  <span className={`text-lg ${active === i ? s.color : active > i ? "text-emerald-400" : "text-slate-600"}`}>
                    {active > i ? "✓" : s.icon}
                  </span>
                  <span className="text-[10px] font-medium text-slate-400 text-center leading-tight">{s.label}</span>
                  {active === i && (
                    <div className="absolute -bottom-7 left-1/2 -translate-x-1/2 whitespace-nowrap text-[10px] text-slate-400 bg-[#0c0c18] px-2 py-0.5 rounded border border-white/[0.06] z-10">
                      {s.detail}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
          {/* Result */}
          <div className="px-5 pb-5 pt-8">
            <div className={`flex items-center gap-3 p-3 rounded-lg border transition-all duration-700 ${
              active >= PIPELINE_STEPS.length - 1 ? "border-emerald-500/30 bg-emerald-900/10" : "border-white/[0.04] opacity-20"
            }`}>
              <span className="text-emerald-400 text-sm">✓</span>
              <span className="font-mono text-xs text-slate-300">Found 47 issues across 6 projects · 0.8s · $0.0014</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Capability cards — grouped
// ─────────────────────────────────────────────────────────────────────────────

type CapCard = {
  icon: string;
  title: string;
  desc: string;
  tag?: string;
  glow: string;
};

const CAPABILITY_GROUPS: { label: string; caption: string; cards: CapCard[] }[] = [
  {
    label: "Autonomous Intelligence",
    caption: "An agent brain that plans, executes, verifies and improves itself — no workflow builder needed.",
    cards: [
      {
        icon: "🧠",
        title: "Three-Role LLM Architecture",
        desc: "Planner, Executor and Verifier are independent models, each tuned for their task. Swap any model per tenant without touching code.",
        tag: "Core AI",
        glow: "from-violet-600/25",
      },
      {
        icon: "🔄",
        title: "Replan on Failure",
        desc: "When the Verifier rejects a step, the Planner receives the failure reason and prior failed attempts — then tries a fundamentally different approach. Up to 15 iterations.",
        tag: "Resilience",
        glow: "from-indigo-600/25",
      },
      {
        icon: "🌲",
        title: "Goal-Tree Decomposition",
        desc: "Complex goals are recursively split into dependency-aware sub-goals executed in parallel by independent agents. Results are LLM-synthesized into one coherent answer.",
        tag: "Parallelism",
        glow: "from-sky-600/25",
      },
      {
        icon: "🤔",
        title: "Chain-of-Thought + Reflection",
        desc: "Agents reason before planning (CoT node) and diagnose failures before replanning (Reflect node). Both are optional per-deployment toggles.",
        tag: "Reasoning",
        glow: "from-blue-600/25",
      },
      {
        icon: "📊",
        title: "7-Dimension Eval Scoring",
        desc: "Every run is scored on task completion, efficiency, accuracy, safety, coherence, SLA and tool relevance. Scores drive automated self-improvement.",
        tag: "Quality",
        glow: "from-cyan-600/25",
      },
      {
        icon: "⚡",
        title: "Bayesian Self-Optimiser",
        desc: "After 5 completions the optimizer runs Thompson sampling, generates an LLM-written improvement, deploys it to 50% traffic, and promotes or rolls back based on eval scores.",
        tag: "Auto-ML",
        glow: "from-purple-600/25",
      },
    ],
  },
  {
    label: "Enterprise Governance",
    caption: "Every action is auditable, approvalable, budget-controlled and compliance-ready.",
    cards: [
      {
        icon: "🛡️",
        title: "Human-in-the-Loop Gate",
        desc: "High-risk keywords (deploy, delete, prod) pause execution and route to the approval queue. Cross-replica durability via Redis — approvals survive process restarts.",
        tag: "HITL",
        glow: "from-amber-600/25",
      },
      {
        icon: "📜",
        title: "Tamper-Evident Audit Trail",
        desc: "SHA-256 hash-chained audit log with < 1 ms write latency via Redis WAL. PII auto-redacted across 14 field types before storage. SIEM export to Splunk, Elastic, Datadog, QRadar, ArcSight.",
        tag: "Compliance",
        glow: "from-orange-600/25",
      },
      {
        icon: "🏛️",
        title: "Glob-Pattern Policy Engine",
        desc: "Policies match tools by glob (github:*), enforce time windows (IANA timezone-aware), support multi-approver quorum, and propagate across replicas via Redis pub/sub within milliseconds.",
        tag: "Policies",
        glow: "from-red-600/25",
      },
      {
        icon: "💰",
        title: "Atomic Budget Enforcement",
        desc: "Per-goal and per-tenant daily budgets enforced via a Redis Lua script in a single round-trip. No overspend possible. Fail-closed in production.",
        tag: "FinOps",
        glow: "from-emerald-600/25",
      },
      {
        icon: "🔒",
        title: "GDPR / SOC2 / PCI-DSS",
        desc: "Right-to-erasure (25 tables, FK-ordered), data portability export, data residency declaration, retention sweeps. Not simulated — implemented in production code.",
        tag: "Regulation",
        glow: "from-teal-600/25",
      },
      {
        icon: "🧬",
        title: "5-Layer Injection Defence",
        desc: "Blocks direct prompts, base64-encoded attacks, ROT13, Unicode homoglyphs, and leetspeak substitutions. Detects PII (SSN, credit card) in outputs before delivery.",
        tag: "Security",
        glow: "from-rose-600/25",
      },
    ],
  },
  {
    label: "Universal Connectivity",
    caption: "Connect to any tool, API or database — built-in or bring your own.",
    cards: [
      {
        icon: "🔌",
        title: "227 Production Connectors",
        desc: "GitHub, Jira, Slack, Salesforce, HubSpot, Stripe, Datadog, Snowflake, Kubernetes, PostgreSQL, AWS, GCP and 215 more. Each connector is certified with read/write parity.",
        tag: "Integrations",
        glow: "from-violet-600/25",
      },
      {
        icon: "🔐",
        title: "9 Auth Modes",
        desc: "Bearer, API key, Basic, Custom header, OAuth2 AC, OAuth2 CC, PKCE, mTLS, HMAC. Full PKCE flow manager with encrypted token refresh. Credentials stored in vault, never in plain text.",
        tag: "Auth",
        glow: "from-indigo-600/25",
      },
      {
        icon: "📡",
        title: "OpenAPI → Connector in Seconds",
        desc: "Import any OpenAPI 3.x spec and every endpoint becomes an agent tool automatically. Any REST API becomes a connector without writing code.",
        tag: "Extensibility",
        glow: "from-sky-600/25",
      },
      {
        icon: "🌐",
        title: "Browser Automation (RPA)",
        desc: "13 Playwright verbs: open URL, click, type, extract text, screenshot, wait for text, fill form, upload file, detect CAPTCHA, and more. Vision analysis on screenshots included.",
        tag: "RPA",
        glow: "from-blue-600/25",
      },
      {
        icon: "🏪",
        title: "8 Deployable Agent Templates",
        desc: "Bug Fix, DevOps Watchdog, E2E Test Generator, HR Onboarding, Sales Follow-up, Support Triage, Code Review, Incident Response. Deploy in one API call.",
        tag: "Marketplace",
        glow: "from-cyan-600/25",
      },
      {
        icon: "⚙️",
        title: "Hot-Swap Connector Registry",
        desc: "Add or remove connectors per tenant at runtime via Redis — no server restart required. Connectors are tenant-scoped and RLS-enforced at the DB layer.",
        tag: "Operations",
        glow: "from-purple-600/25",
      },
    ],
  },
  {
    label: "Multi-Agent Civilization",
    caption: "From a single agent to a self-governing society of AI agents working in concert.",
    cards: [
      {
        icon: "🌍",
        title: "Agent Civilization System",
        desc: "A Constitutional framework governs a society of agents: spawn limits, budget allocation, autonomy ceilings, reputation scoring. Agents debate, vote, and collectively learn.",
        tag: "Emergent AI",
        glow: "from-violet-600/25",
      },
      {
        icon: "⚖️",
        title: "Debate & Voting",
        desc: "3–5 agents independently propose solutions, cross-critique each other, then vote. Thompson-sampled consensus reduces hallucination on high-stakes decisions.",
        tag: "Multi-Agent",
        glow: "from-indigo-600/25",
      },
      {
        icon: "👤",
        title: "Supervisor Orchestration",
        desc: "A top-level Supervisor decomposes goals into 2–6 sub-tasks, dispatches them concurrently across the registry, monitors SSE streams, and synthesises results.",
        tag: "Orchestration",
        glow: "from-sky-600/25",
      },
      {
        icon: "🔗",
        title: "Agent-to-Agent Protocol (A2A)",
        desc: "Standardised capability declaration (AgentCard), task delegation (A2ATask), and cross-agent result passing. Compatible with emerging A2A protocol standards.",
        tag: "Protocol",
        glow: "from-blue-600/25",
      },
      {
        icon: "📚",
        title: "Shared Blackboard & Memory",
        desc: "Agents post findings to a shared knowledge board. Long-term memory is stored as pgvector embeddings — recalled at planning time to avoid repeating mistakes.",
        tag: "Memory",
        glow: "from-cyan-600/25",
      },
      {
        icon: "🧭",
        title: "NL Scheduler",
        desc: "\"Every weekday at 9 AM UTC\" → CRON spec. \"Every hour\" → INTERVAL. Compound schedules from one sentence. Natural language becomes a trigger pipeline.",
        tag: "Scheduling",
        glow: "from-purple-600/25",
      },
    ],
  },
];

function CapCard({ icon, title, desc, tag, glow, delay }: CapCard & { delay: number }) {
  const { ref, on } = useReveal(0.08);
  return (
    <div
      ref={ref}
      className={`reveal ${on ? "reveal-on" : ""} group relative overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.025] hover:border-violet-500/25 hover:bg-white/[0.04] transition-all duration-300 cursor-default p-5`}
      style={{ transitionDelay: `${delay}ms`, animationDelay: `${delay}ms` }}
    >
      <div className={`absolute inset-0 bg-gradient-to-br ${glow} to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500`} />
      <div className="relative">
        <div className="flex items-start justify-between mb-3">
          <span className="text-2xl">{icon}</span>
          {tag && (
            <span className="text-[10px] font-semibold tracking-wider uppercase text-slate-500 border border-white/[0.07] rounded-full px-2 py-0.5">
              {tag}
            </span>
          )}
        </div>
        <h3 className="font-semibold text-white text-sm mb-2 leading-snug">{title}</h3>
        <p className="text-xs text-slate-400 leading-relaxed">{desc}</p>
      </div>
    </div>
  );
}

function CapabilitySection({ label, caption, cards }: typeof CAPABILITY_GROUPS[0]) {
  const { ref, on } = useReveal(0.1);
  return (
    <div className="py-20">
      <div ref={ref} className={`reveal ${on ? "reveal-on" : ""} text-center mb-12`}>
        <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-3">{label}</h2>
        <p className="text-slate-400 max-w-2xl mx-auto">{caption}</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {cards.map((c, i) => (
          <CapCard key={c.title} {...c} delay={i * 60} />
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Connector marquee
// ─────────────────────────────────────────────────────────────────────────────

const CONNECTORS_ROW1 = [
  { n: "Jira", c: "#2684FF" }, { n: "GitHub", c: "#e2e8f0" }, { n: "Slack", c: "#E01E5A" },
  { n: "Salesforce", c: "#00A1E0" }, { n: "HubSpot", c: "#FF7A59" }, { n: "Datadog", c: "#632CA6" },
  { n: "Stripe", c: "#635BFF" }, { n: "PagerDuty", c: "#06AC38" }, { n: "Linear", c: "#5E6AD2" },
  { n: "Notion", c: "#ffffff" }, { n: "Confluence", c: "#2684FF" }, { n: "AWS", c: "#FF9900" },
];
const CONNECTORS_ROW2 = [
  { n: "GitLab", c: "#FC6D26" }, { n: "Asana", c: "#F06A6A" }, { n: "Snowflake", c: "#29B5E8" },
  { n: "Kubernetes", c: "#326CE5" }, { n: "Zendesk", c: "#03363D" }, { n: "Intercom", c: "#1F8DED" },
  { n: "Google Cloud", c: "#4285F4" }, { n: "Terraform", c: "#7B42BC" }, { n: "Shopify", c: "#96BF48" },
  { n: "Okta", c: "#007DC1" }, { n: "Figma", c: "#F24E1E" }, { n: "Monday.com", c: "#FF3D57" },
];

function ConnectorTag({ n, c }: { n: string; c: string }) {
  return (
    <div className="flex-shrink-0 flex items-center gap-2 px-4 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] hover:border-white/15 transition-colors">
      <span className="h-2 w-2 rounded-full flex-shrink-0" style={{ background: c }} />
      <span className="text-sm text-slate-300 whitespace-nowrap font-medium">{n}</span>
    </div>
  );
}

function ConnectorMarquee() {
  return (
    <div className="py-20">
      <div className="text-center mb-12">
        <div className="inline-flex items-center gap-2 text-xs font-semibold tracking-widest uppercase text-violet-400 mb-4">
          <span className="h-px w-8 bg-violet-400/50" /> Universal Connectivity <span className="h-px w-8 bg-violet-400/50" />
        </div>
        <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-3">
          227 production-certified connectors
        </h2>
        <p className="text-slate-400 max-w-xl mx-auto">
          Every connector ships with certified read/write tool parity, encrypted credential storage and hot-swap registration.
        </p>
      </div>
      {[CONNECTORS_ROW1, CONNECTORS_ROW2].map((row, ri) => (
        <div key={ri} className="relative overflow-hidden mb-3">
          <div className={`flex gap-3 ${ri === 0 ? "animate-marquee" : "animate-marquee-reverse"}`}>
            {[...row, ...row].map((c, i) => <ConnectorTag key={i} {...c} />)}
          </div>
          <div className="absolute left-0 top-0 bottom-0 w-24 bg-gradient-to-r from-[#06060e] to-transparent pointer-events-none z-10" />
          <div className="absolute right-0 top-0 bottom-0 w-24 bg-gradient-to-l from-[#06060e] to-transparent pointer-events-none z-10" />
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Stats
// ─────────────────────────────────────────────────────────────────────────────

const STATS = [
  { val: 227, suffix: "+", label: "Certified connectors" },
  { val: 9, suffix: "", label: "Auth protocols supported" },
  { val: 15, suffix: "", label: "Multi-agent architectures" },
  { val: 7, suffix: "", label: "Eval quality dimensions" },
  { val: 13, suffix: "", label: "RPA browser actions" },
  { val: 5, suffix: "", label: "Attack vector defences" },
];

function StatsSection() {
  const { ref, on } = useReveal(0.2);
  const counts = STATS.map(s => useCounter(s.val, 2000, on)); // eslint-disable-line react-hooks/rules-of-hooks
  return (
    <div className="py-20">
      <div className="relative rounded-2xl overflow-hidden border border-white/[0.06] bg-gradient-to-br from-violet-900/15 via-[#0c0c18] to-indigo-900/10 p-12 md:p-16">
        <div className="absolute inset-0 landing-grid-bg opacity-10" />
        <div ref={ref} className="relative text-center mb-12">
          <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-3">
            Built for production at every layer
          </h2>
          <p className="text-slate-400">Not a demo. Not a prototype. Every number comes from shipped code.</p>
        </div>
        <div className="relative grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-8">
          {STATS.map((s, i) => (
            <div key={s.label} className="text-center">
              <div className="font-display text-4xl md:text-5xl font-bold text-white tabular-nums">
                {counts[i].toLocaleString()}{s.suffix}
              </div>
              <div className="text-xs text-slate-500 mt-2 leading-tight">{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Architecture visual — horizontal flow
// ─────────────────────────────────────────────────────────────────────────────

const ARCH_LAYERS = [
  {
    title: "Intelligence Layer",
    items: ["LangGraph agent loop", "3-role LLM routing", "CoT & Reflection", "Goal-tree decomp."],
    color: "border-violet-500/30 bg-violet-900/10",
    dot: "bg-violet-500",
  },
  {
    title: "Connectivity Layer",
    items: ["227 connectors", "9 auth protocols", "OAuth2 + PKCE", "OpenAPI importer"],
    color: "border-sky-500/30 bg-sky-900/10",
    dot: "bg-sky-500",
  },
  {
    title: "Governance Layer",
    items: ["HITL approval gate", "Policy engine", "Cost controller", "Hash-chain audit"],
    color: "border-amber-500/30 bg-amber-900/10",
    dot: "bg-amber-500",
  },
  {
    title: "Memory Layer",
    items: ["pgvector RAG", "Execution memory", "Semantic cache", "Federated search"],
    color: "border-emerald-500/30 bg-emerald-900/10",
    dot: "bg-emerald-500",
  },
  {
    title: "Reliability Layer",
    items: ["Circuit breakers", "Per-tenant bulkhead", "LIFO rollback", "Dedup + idempotency"],
    color: "border-rose-500/30 bg-rose-900/10",
    dot: "bg-rose-500",
  },
];

function ArchSection() {
  const { ref, on } = useReveal(0.1);
  return (
    <div className="py-20">
      <div ref={ref} className={`reveal ${on ? "reveal-on" : ""} text-center mb-14`}>
        <div className="inline-flex items-center gap-2 text-xs font-semibold tracking-widest uppercase text-slate-500 mb-4">
          <span className="h-px w-8 bg-slate-600" /> Architecture
        </div>
        <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-3">
          Five production layers, zero compromise
        </h2>
        <p className="text-slate-400 max-w-xl mx-auto">
          Each layer is independently deployable, fully observable, and designed to fail safely.
        </p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {ARCH_LAYERS.map(({ title, items, color, dot }, i) => {
          const { ref: r, on: v } = useReveal(0.1); // eslint-disable-line react-hooks/rules-of-hooks
          return (
            <div
              key={title}
              ref={r}
              className={`reveal ${v ? "reveal-on" : ""} rounded-xl border p-5 ${color}`}
              style={{ transitionDelay: `${i * 100}ms` }}
            >
              <div className="flex items-center gap-2 mb-4">
                <span className={`h-2 w-2 rounded-full ${dot}`} />
                <span className="text-xs font-semibold text-white">{title}</span>
              </div>
              <ul className="space-y-2">
                {items.map(it => (
                  <li key={it} className="text-xs text-slate-400 flex items-center gap-1.5">
                    <span className="h-px w-3 bg-slate-600 flex-shrink-0" />{it}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Use-case spotlights
// ─────────────────────────────────────────────────────────────────────────────

const SPOTLIGHTS = [
  {
    role: "Engineering Teams",
    headline: "Turn every incident into a resolved ticket — automatically.",
    bullets: [
      "Monitors Datadog alerts → pages PagerDuty → creates Jira P1",
      "Pulls relevant logs, diagnoses root cause with LLM reasoning",
      "Proposes a fix, opens a PR, assigns reviewer",
      "HITL gate pauses for approval before merging to prod",
    ],
    connectors: ["Datadog", "PagerDuty", "Jira", "GitHub"],
    accent: "from-violet-600 to-indigo-600",
    border: "border-violet-500/20",
  },
  {
    role: "Sales & Revenue",
    headline: "Close pipeline while your team sleeps.",
    bullets: [
      "Monitors Salesforce for deals idle > 7 days",
      "Drafts personalised follow-up via HubSpot with deal context",
      "Updates CRM stage, logs activity, creates next task",
      "Weekly pipeline health report delivered to Slack",
    ],
    connectors: ["Salesforce", "HubSpot", "Slack"],
    accent: "from-sky-600 to-cyan-600",
    border: "border-sky-500/20",
  },
  {
    role: "HR & Operations",
    headline: "Every new hire fully set up before their first day.",
    bullets: [
      "Triggered by ATS hire event in Greenhouse",
      "Provisions accounts across Okta, Slack, Jira, Notion",
      "Assigns onboarding tasks, sends welcome sequence",
      "Escalates to HR manager if any step fails approval",
    ],
    connectors: ["Greenhouse", "Okta", "Slack", "Notion"],
    accent: "from-emerald-600 to-teal-600",
    border: "border-emerald-500/20",
  },
];

function SpotlightSection() {
  const { ref, on } = useReveal(0.1);
  return (
    <div className="py-20">
      <div ref={ref} className={`reveal ${on ? "reveal-on" : ""} text-center mb-14`}>
        <div className="inline-flex items-center gap-2 text-xs font-semibold tracking-widest uppercase text-slate-500 mb-4">
          <span className="h-px w-8 bg-slate-600" /> Use Cases
        </div>
        <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-3">
          One platform. Every team.
        </h2>
        <p className="text-slate-400 max-w-xl mx-auto">
          AgentVerse agents work across functions without custom integrations or manual handoffs.
        </p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {SPOTLIGHTS.map(({ role, headline, bullets, connectors, accent, border }, i) => {
          const { ref: r, on: v } = useReveal(0.1); // eslint-disable-line react-hooks/rules-of-hooks
          return (
            <div
              key={role}
              ref={r}
              className={`reveal ${v ? "reveal-on" : ""} relative rounded-2xl border ${border} bg-white/[0.025] p-6 flex flex-col`}
              style={{ transitionDelay: `${i * 120}ms` }}
            >
              <div className={`inline-flex self-start text-xs font-semibold px-2.5 py-1 rounded-full bg-gradient-to-r ${accent} text-white mb-4`}>
                {role}
              </div>
              <h3 className="font-display font-bold text-white text-base mb-4 leading-snug">{headline}</h3>
              <ul className="space-y-2.5 flex-1">
                {bullets.map((b, j) => (
                  <li key={j} className="flex items-start gap-2 text-sm text-slate-400">
                    <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-slate-500 flex-shrink-0" />
                    {b}
                  </li>
                ))}
              </ul>
              <div className="mt-5 pt-4 border-t border-white/[0.05] flex flex-wrap gap-1.5">
                {connectors.map(c => (
                  <span key={c} className="text-[10px] font-medium text-slate-500 border border-white/[0.06] rounded px-1.5 py-0.5">
                    {c}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Observability strip
// ─────────────────────────────────────────────────────────────────────────────

const OBS_ITEMS = [
  { icon: "📡", title: "Live SSE Execution Stream", desc: "Every agent step streamed in real time. No polling. Events arrive the moment they're emitted — across Celery workers via Redis pub/sub." },
  { icon: "🔭", title: "OpenTelemetry Tracing", desc: "Named spans for plan, execute and verify with tenant_id, iteration and step_description attributes. Export to Jaeger or any OTLP collector." },
  { icon: "🧬", title: "Goal DNA Visualisation", desc: "Force-graph exploration of every decision, tool call and verification in a goal run. Diff two runs. Ghost-run a goal with a different strategy." },
  { icon: "📈", title: "Cost & Latency Analytics", desc: "Per-goal, per-agent, per-tenant cost tracking with daily budget enforcement. Real token-based billing — not LLM call counts." },
];

function ObsSection() {
  const { ref, on } = useReveal(0.1);
  return (
    <div className="py-20">
      <div ref={ref} className={`reveal ${on ? "reveal-on" : ""} text-center mb-14`}>
        <div className="inline-flex items-center gap-2 text-xs font-semibold tracking-widest uppercase text-slate-500 mb-4">
          <span className="h-px w-8 bg-slate-600" /> Observability
        </div>
        <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-3">
          Full transparency into every decision
        </h2>
        <p className="text-slate-400 max-w-xl mx-auto">
          See exactly what your agents thought, why they called which tools, and where costs went.
        </p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        {OBS_ITEMS.map(({ icon, title, desc }, i) => {
          const { ref: r, on: v } = useReveal(0.1); // eslint-disable-line react-hooks/rules-of-hooks
          return (
            <div
              key={title}
              ref={r}
              className={`reveal ${v ? "reveal-on" : ""} p-5 rounded-2xl border border-white/[0.06] bg-white/[0.02] hover:border-white/[0.12] transition-colors`}
              style={{ transitionDelay: `${i * 80}ms` }}
            >
              <div className="text-2xl mb-3">{icon}</div>
              <h3 className="font-semibold text-white text-sm mb-2">{title}</h3>
              <p className="text-xs text-slate-400 leading-relaxed">{desc}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// CTA
// ─────────────────────────────────────────────────────────────────────────────

function CTASection({ onStart }: { onStart: () => void }) {
  const { ref, on } = useReveal(0.2);
  return (
    <div className="py-24">
      <div className="relative rounded-2xl overflow-hidden border border-white/[0.07]">
        <div className="absolute inset-0 bg-gradient-to-br from-violet-900/30 via-[#0c0c18] to-indigo-900/20" />
        <div className="absolute inset-0 landing-grid-bg opacity-10" />
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[200px] bg-violet-700/20 blur-[80px] rounded-full pointer-events-none" />
        <div ref={ref} className={`reveal ${on ? "reveal-on" : ""} relative py-20 px-8 text-center`}>
          <div className="inline-flex items-center gap-2 text-xs font-semibold tracking-widest uppercase text-violet-400 mb-6">
            <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-pulse" />
            Ready to deploy
          </div>
          <h2 className="font-display text-4xl md:text-5xl font-bold text-white mb-5 leading-tight">
            The operating system for<br />
            <span className="bg-gradient-to-r from-violet-400 via-indigo-300 to-sky-400 bg-clip-text text-transparent">
              autonomous enterprise AI.
            </span>
          </h2>
          <p className="text-slate-400 text-lg max-w-lg mx-auto mb-10">
            Multi-tenant. Governance-first. Connects to everything.
            Submit a goal — your agents handle the rest.
          </p>
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <button
              onClick={onStart}
              className="group relative px-8 py-4 font-semibold text-white bg-gradient-to-r from-violet-600 to-indigo-600 rounded-xl hover:shadow-2xl hover:shadow-violet-900/40 transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
            >
              <span className="absolute inset-0 rounded-xl bg-white/10 opacity-0 group-hover:opacity-100 transition-opacity" />
              Launch your first agent →
            </button>
            <button
              onClick={onStart}
              className="px-8 py-4 font-medium text-slate-300 border border-white/[0.1] rounded-xl hover:border-white/25 hover:text-white transition-all duration-200 hover:bg-white/[0.03]"
            >
              Sign in to dashboard
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Nav
// ─────────────────────────────────────────────────────────────────────────────

function NavBar({ onStart }: { onStart: () => void }) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const fn = () => setScrolled(window.scrollY > 24);
    window.addEventListener("scroll", fn, { passive: true });
    return () => window.removeEventListener("scroll", fn);
  }, []);

  return (
    <nav className={`fixed top-0 inset-x-0 z-50 transition-all duration-300 ${
      scrolled ? "bg-[#06060e]/90 backdrop-blur-md border-b border-white/[0.06]" : "bg-transparent"
    }`}>
      <div className="max-w-7xl mx-auto flex items-center justify-between px-6 md:px-10 h-16">
        {/* Brand */}
        <div className="flex items-center gap-2.5">
          <div className="h-7 w-7 rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center">
            <span className="text-[11px] font-bold text-white">AV</span>
          </div>
          <span className="font-display font-semibold text-white text-base tracking-tight">AgentVerse</span>
        </div>
        {/* Links */}
        <div className="hidden md:flex items-center gap-7">
          {["Platform", "Connectors", "Governance", "Use Cases"].map(l => (
            <a key={l} href={`#${l.toLowerCase().replace(" ", "-")}`}
              className="text-sm text-slate-400 hover:text-white transition-colors">
              {l}
            </a>
          ))}
        </div>
        {/* Actions */}
        <div className="flex items-center gap-3">
          <button onClick={onStart} className="text-sm text-slate-400 hover:text-white transition-colors px-3 py-1.5">
            Sign in
          </button>
          <button
            onClick={onStart}
            className="px-4 py-2 text-sm font-medium bg-violet-600 hover:bg-violet-500 text-white rounded-lg transition-colors shadow-lg shadow-violet-900/30"
          >
            Get started
          </button>
        </div>
      </div>
    </nav>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Footer
// ─────────────────────────────────────────────────────────────────────────────

function Footer() {
  return (
    <footer className="border-t border-white/[0.05] py-14 px-6 md:px-10">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between gap-10">
        <div className="max-w-xs">
          <div className="flex items-center gap-2 mb-4">
            <div className="h-6 w-6 rounded-md bg-gradient-to-br from-violet-500 to-indigo-600 flex items-center justify-center">
              <span className="text-[9px] font-bold text-white">AV</span>
            </div>
            <span className="font-display font-semibold text-white">AgentVerse</span>
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            A multi-tenant operating system for autonomous AI agents. Governance-first. Enterprise-ready. Open architecture.
          </p>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-8 text-sm">
          {[
            { h: "Platform", ls: ["Autonomous Agents", "227 Connectors", "Governance", "Observability", "Marketplace"] },
            { h: "Enterprise", ls: ["HITL Approval", "GDPR / SOC2", "Audit Trail", "SIEM Integration", "Multi-Tenancy"] },
            { h: "Developers", ls: ["Python SDK", "TypeScript SDK", "GitHub Action", "OpenAPI Docs", "Certification"] },
          ].map(({ h, ls }) => (
            <div key={h}>
              <div className="text-white font-medium mb-3">{h}</div>
              {ls.map(l => (
                <div key={l} className="mb-1.5">
                  <a href="#" className="text-slate-500 hover:text-slate-300 transition-colors text-xs">{l}</a>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
      <div className="max-w-7xl mx-auto mt-10 pt-6 border-t border-white/[0.04] flex flex-col sm:flex-row justify-between gap-2 text-[11px] text-slate-600">
        <span>© 2026 AgentVerse. The autonomous AI operating system.</span>
        <span>Multi-tenant · Governance-first · 227 connectors</span>
      </div>
    </footer>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Root
// ─────────────────────────────────────────────────────────────────────────────

export function LandingPage() {
  const navigate = useNavigate();
  const go = useCallback(() => navigate("/auth"), [navigate]);

  return (
    <div className="min-h-screen bg-[#06060e] text-white overflow-x-hidden">

      {/* Background atmosphere */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute inset-0 landing-grid-bg opacity-20" />
        <div className="absolute top-0 left-1/3 w-[700px] h-[700px] bg-violet-900/15 rounded-full blur-[130px] animate-orb-drift" />
        <div className="absolute top-1/2 right-1/4 w-[500px] h-[500px] bg-indigo-900/12 rounded-full blur-[100px] animate-orb-drift-slow" />
        <div className="absolute bottom-1/4 left-1/4 w-[400px] h-[400px] bg-sky-900/10 rounded-full blur-[90px]" />
        <div className="absolute inset-0 landing-noise opacity-[0.025]" />
      </div>

      <NavBar onStart={go} />

      <div className="relative z-10 max-w-7xl mx-auto px-6 md:px-10">

        {/* ── Hero ─────────────────────────────────────────────────────── */}
        <section className="pt-36 pb-16 text-center">
          {/* Category badge */}
          <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-violet-500/25 bg-violet-500/8 text-violet-300 text-xs font-medium mb-8 animate-fade-in">
            <span className="h-1.5 w-1.5 rounded-full bg-violet-400 animate-pulse" />
            Autonomous AI Operating System · Multi-tenant · Enterprise-grade
          </div>

          {/* Headline */}
          <h1 className="font-display text-5xl md:text-7xl lg:text-[80px] font-extrabold text-white leading-[1.03] tracking-tight mb-7 animate-fade-in-up" style={{ animationDelay: "80ms" }}>
            Your agents.<br />
            <span className="bg-gradient-to-r from-violet-400 via-indigo-300 to-sky-400 bg-clip-text text-transparent">
              Every tool. Zero code.
            </span>
          </h1>

          {/* Typewriter */}
          <div className="text-xl md:text-2xl text-slate-300 mb-4 animate-fade-in-up h-8" style={{ animationDelay: "180ms" }}>
            Your agent&nbsp;<Typewriter />
          </div>

          <p className="text-slate-400 text-base md:text-lg max-w-2xl mx-auto mb-10 leading-relaxed animate-fade-in-up" style={{ animationDelay: "260ms" }}>
            Submit a natural language goal. AgentVerse plans, executes across{" "}
            <span className="text-white font-medium">227 real-world connectors</span>, verifies results, and replans on failure —
            with enterprise governance baked in at every layer.
          </p>

          {/* CTAs */}
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 animate-fade-in-up" style={{ animationDelay: "340ms" }}>
            <button
              onClick={go}
              className="group relative px-7 py-3.5 font-semibold text-white bg-gradient-to-r from-violet-600 to-indigo-600 rounded-xl hover:shadow-xl hover:shadow-violet-900/40 transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
            >
              <span className="absolute inset-0 rounded-xl bg-white/10 opacity-0 group-hover:opacity-100 transition-opacity" />
              Launch your first agent →
            </button>
            <a href="#platform" className="px-7 py-3.5 font-medium text-slate-300 border border-white/[0.1] rounded-xl hover:border-white/20 hover:text-white transition-all duration-200 hover:bg-white/[0.03]">
              Explore the platform
            </a>
          </div>

          {/* Quick proof row */}
          <div className="flex flex-wrap justify-center gap-8 mt-14 animate-fade-in-up" style={{ animationDelay: "440ms" }}>
            {[
              { n: "227+", l: "connectors" },
              { n: "9", l: "auth protocols" },
              { n: "7", l: "eval dimensions" },
              { n: "5", l: "LLM providers" },
              { n: "<1ms", l: "audit write latency" },
              { n: "0", l: "hardcoded workflows" },
            ].map(({ n, l }) => (
              <div key={l} className="flex flex-col items-center">
                <span className="font-display text-2xl font-bold text-white">{n}</span>
                <span className="text-[11px] text-slate-500 mt-0.5">{l}</span>
              </div>
            ))}
          </div>
        </section>

        {/* ── Pipeline Demo ─────────────────────────────────────────────── */}
        <section id="platform" className="py-6">
          <PipelineDemo />
        </section>

        {/* ── All capability groups ──────────────────────────────────────── */}
        {CAPABILITY_GROUPS.map(g => (
          <CapabilitySection key={g.label} {...g} />
        ))}

        {/* ── Architecture ──────────────────────────────────────────────── */}
        <ArchSection />

        {/* ── Connectors marquee ────────────────────────────────────────── */}
        <ConnectorMarquee />

        {/* ── Observability ─────────────────────────────────────────────── */}
        <ObsSection />

        {/* ── Use cases ─────────────────────────────────────────────────── */}
        <SpotlightSection />

        {/* ── Stats ─────────────────────────────────────────────────────── */}
        <StatsSection />

        {/* ── CTA ───────────────────────────────────────────────────────── */}
        <CTASection onStart={go} />

      </div>

      <Footer />
    </div>
  );
}
