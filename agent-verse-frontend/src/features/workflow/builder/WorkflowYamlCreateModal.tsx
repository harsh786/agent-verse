/**
 * WorkflowYamlCreateModal — author a workflow from YAML and create it.
 *
 * Real YAML (js-yaml) → workflow definition → POST /api/v1/workflows. The new
 * workflow lands in the list immediately and opens in the visual builder, so
 * YAML-authored and canvas-authored workflows are the same first-class object.
 * Ships starter templates for common real-world automations so a blank page is
 * never the starting point.
 */
import { useMemo, useState } from 'react';
import yaml from 'js-yaml';
import { useMutation } from '@tanstack/react-query';
import { X, FileCode2, AlertCircle, Check, Sparkles, Loader2 } from 'lucide-react';
import { workflowEngineApi } from '../../../lib/api/client';

// ── Starter templates (real-world automations) ────────────────────────────────

const TEMPLATES: { id: string; label: string; hint: string; yaml: string }[] = [
  {
    id: 'invoice',
    label: 'Invoice scan → extract → post',
    hint: 'OCR a document, extract fields with an LLM, POST to a system',
    yaml: `name: Invoice Scan Automation
triggers:
  - type: webhook
    webhook:
      auth: none
steps:
  - id: ocr
    name: OCR the invoice
    type: ocr
    input:
      file_path: "{{inputs.file_path}}"
  - id: extract
    name: Extract fields
    type: llm
    timeout: 180s
    depends_on: [ocr]
    prompt: |
      From this invoice OCR text return ONLY compact JSON with keys
      invoice_number, date, total. Text:
      {{steps.ocr.output.raw_text}}
  - id: post
    name: Post to external system
    type: http
    method: POST
    url: https://httpbin.org/post
    depends_on: [extract]
    request_body:
      extracted: "{{steps.extract.output}}"
`,
  },
  {
    id: 'rag',
    label: 'RAG knowledge Q&A',
    hint: 'Answer a question grounded in a knowledge collection',
    yaml: `name: Knowledge Q&A
triggers:
  - type: webhook
    webhook:
      auth: none
inputs:
  question:
    type: string
    required: true
steps:
  - id: answer
    name: Answer from knowledge
    type: llm
    timeout: 180s
    rag:
      collection: my-collection
      top_k: 4
    prompt: |
      Using ONLY the provided context, answer the question.
      Question: {{inputs.question}}
`,
  },
  {
    id: 'schedule',
    label: 'Scheduled daily digest',
    hint: 'Runs on a cron, fetches data, summarizes, notifies',
    yaml: `name: Daily Digest
triggers:
  - type: schedule
    schedule:
      cron: "0 9 * * *"
      timezone: UTC
steps:
  - id: fetch
    name: Fetch source data
    type: http
    method: GET
    url: https://jsonplaceholder.typicode.com/posts?_limit=5
  - id: summarize
    name: Summarize
    type: llm
    timeout: 180s
    depends_on: [fetch]
    prompt: |
      Summarize these items into a 3-bullet digest:
      {{steps.fetch.output}}
`,
  },
  {
    id: 'approval',
    label: 'HTTP + human approval',
    hint: 'Fetch, require a human decision, then act',
    yaml: `name: Approval Gate
triggers:
  - type: webhook
    webhook:
      auth: none
steps:
  - id: fetch
    name: Fetch request
    type: http
    method: GET
    url: https://httpbin.org/get
  - id: review
    name: Human review
    type: hitl
    depends_on: [fetch]
    timeout: 24h
  - id: act
    name: Act on approval
    type: http
    method: POST
    url: https://httpbin.org/post
    depends_on: [review]
`,
  },
];

// ── Validation ────────────────────────────────────────────────────────────────

function parseDefinition(text: string): { def?: Record<string, unknown>; error?: string } {
  if (!text.trim()) return { error: 'Empty definition' };
  let parsed: unknown;
  try {
    parsed = yaml.load(text);
  } catch (e) {
    return { error: e instanceof Error ? e.message : 'Invalid YAML' };
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return { error: 'Top level must be a mapping (name, steps, …)' };
  }
  const def = parsed as Record<string, unknown>;
  if (!def.name || typeof def.name !== 'string') {
    return { error: 'A top-level "name" is required' };
  }
  if (!Array.isArray(def.steps)) {
    return { error: 'A "steps" list is required' };
  }
  return { def };
}

// ── Modal ─────────────────────────────────────────────────────────────────────

export function WorkflowYamlCreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (workflowId: string) => void;
}) {
  const [text, setText] = useState(TEMPLATES[0].yaml);
  const [activeTemplate, setActiveTemplate] = useState(TEMPLATES[0].id);

  const { def, error } = useMemo(() => parseDefinition(text), [text]);
  const stepCount = def && Array.isArray(def.steps) ? def.steps.length : 0;

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!def) throw new Error(error || 'Invalid definition');
      const wf = await workflowEngineApi.create({
        name: String(def.name),
        description: typeof def.description === 'string' ? def.description : '',
        definition: def,
      });
      return wf;
    },
    onSuccess: (wf) => onCreated(wf.id),
  });

  const applyTemplate = (id: string) => {
    const t = TEMPLATES.find((x) => x.id === id);
    if (t) {
      setText(t.yaml);
      setActiveTemplate(id);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Create workflow from YAML"
      onClick={onClose}
    >
      <div
        className="jarvis-pop-in w-full max-w-3xl max-h-[88vh] flex flex-col rounded-2xl border
                   border-white/10 bg-[#0F1117] shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-white/10">
          <div className="w-9 h-9 rounded-xl bg-sky-500/15 flex items-center justify-center">
            <FileCode2 className="h-4.5 w-4.5 text-sky-400" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-semibold text-[#F1F5F9]">Create workflow from YAML</h2>
            <p className="text-xs text-[#F1F5F9]/40">
              Paste or edit a definition — it appears in your workflow list instantly.
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#F1F5F9]/40 hover:text-[#F1F5F9] hover:bg-white/5"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Templates */}
        <div className="flex items-center gap-2 px-5 py-3 border-b border-white/5 overflow-x-auto">
          <Sparkles className="h-3.5 w-3.5 text-[#F1F5F9]/30 shrink-0" aria-hidden />
          {TEMPLATES.map((t) => (
            <button
              key={t.id}
              onClick={() => applyTemplate(t.id)}
              title={t.hint}
              className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                activeTemplate === t.id
                  ? 'bg-sky-600 text-white'
                  : 'bg-white/[0.04] text-[#F1F5F9]/60 hover:text-[#F1F5F9] hover:bg-white/[0.08]'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Editor */}
        <div className="flex-1 min-h-0 relative">
          <textarea
            value={text}
            onChange={(e) => { setText(e.target.value); setActiveTemplate(''); }}
            spellCheck={false}
            className="w-full h-full min-h-[320px] bg-[#0A0D14] text-xs text-[#CBD5E1] font-mono
                       p-4 resize-none outline-none leading-relaxed"
            aria-label="Workflow YAML"
          />
        </div>

        {/* Footer */}
        <div className="flex items-center gap-3 px-5 py-4 border-t border-white/10">
          {error ? (
            <span className="flex items-center gap-1.5 text-xs text-red-400 min-w-0">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{error}</span>
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs text-emerald-400">
              <Check className="h-3.5 w-3.5" />
              Valid · {stepCount} step{stepCount !== 1 ? 's' : ''}
            </span>
          )}
          {createMutation.isError && (
            <span className="text-xs text-red-400 truncate">
              {(createMutation.error as Error)?.message || 'Create failed'}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-sm text-[#F1F5F9]/60 hover:text-[#F1F5F9]
                         hover:bg-white/5 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={() => createMutation.mutate()}
              disabled={!!error || createMutation.isPending}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500
                         text-white text-sm font-medium transition-colors disabled:opacity-50
                         disabled:cursor-not-allowed"
            >
              {createMutation.isPending
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <FileCode2 className="h-4 w-4" />}
              Create workflow
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default WorkflowYamlCreateModal;
