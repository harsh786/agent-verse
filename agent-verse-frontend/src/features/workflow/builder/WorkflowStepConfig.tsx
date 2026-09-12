/**
 * WorkflowStepConfig — right-panel config form for the selected node.
 *
 * Animations:
 * - Fields animate in with stagger
 * - Input focus: border glow transition
 * - Close button: hover scale
 */

import { motion } from 'framer-motion';
import { springs } from '../design/motion';
import { X, Settings, Clock, Info, ShieldAlert } from 'lucide-react';
import type { Node } from '@xyflow/react';
import { NODE_ICONS, NODE_LABELS } from '../design/tokens';
import type { WorkflowNodeData } from './nodes/BaseWorkflowNode';
import { TextAreaField, NumberField, ToggleField } from './config-fields/FormFields';
import { KeyValueEditor } from './config-fields/KeyValueEditor';
import { CollapsibleSection } from './config-fields/AdvancedSection';

interface StepConfigProps {
  node: Node;
  onUpdate: (updates: Partial<WorkflowNodeData>) => void;
  onClose: () => void;
}

// Shared shape for a per-type config panel. `nodeId` lets dict editors re-seed
// their local draft state when the selected node changes (see KeyValueEditor).
interface PanelProps {
  data: WorkflowNodeData;
  onUpdate: (u: Partial<WorkflowNodeData>) => void;
  nodeId: string;
}

// Merge a patch into a nested dict field, dropping keys whose value is cleared.
function patchDict(
  current: unknown,
  patch: Record<string, unknown>,
): Record<string, unknown> {
  const base = (current && typeof current === 'object' ? current : {}) as Record<string, unknown>;
  const next: Record<string, unknown> = { ...base };
  for (const [k, v] of Object.entries(patch)) {
    if (v === '' || v === undefined) delete next[k];
    else next[k] = v;
  }
  return next;
}

// ── Generic field editors ─────────────────────────────────────────────────────

function TextField({
  label, value, onChange, placeholder, multiline = false,
  description, mono = false,
}: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; multiline?: boolean; description?: string; mono?: boolean;
}) {
  const id = `cfg-${label.replace(/\s+/g, '-').toLowerCase()}`;
  const cls = `w-full px-3 py-2 rounded-xl bg-[#0F1826]/5 border border-white/10 text-white/90
    placeholder-white/30 text-xs focus:outline-none focus:ring-2 focus:ring-sky-500
    focus:border-transparent ${mono ? 'font-mono' : ''}`;
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-white/50 mb-1">
        {label}
      </label>
      {multiline ? (
        <textarea
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={4}
          className={`${cls} resize-none`}
        />
      ) : (
        <input
          id={id}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={cls}
        />
      )}
      {description && <p className="text-xs text-white/30 mt-1">{description}</p>}
    </div>
  );
}

function SelectField({
  label, value, options, onChange,
}: {
  label: string; value: string;
  options: { label: string; value: string }[];
  onChange: (v: string) => void;
}) {
  const id = `cfg-select-${label.replace(/\s+/g, '-').toLowerCase()}`;
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-white/50 mb-1">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-2 rounded-xl bg-[#0F1826]/5 border border-white/10 text-white/90
                   text-xs focus:outline-none focus:ring-2 focus:ring-sky-500"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-slate-800">{o.label}</option>
        ))}
      </select>
    </div>
  );
}

// ── Cron humanizer (shared by the Trigger panel) ──────────────────────────────

function humanCron(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length < 5) return '';
  const [min, hr, dom, , dow] = parts;
  const at = (h: string, m: string) => {
    const hh = Number(h), mm = Number(m);
    if (Number.isNaN(hh) || Number.isNaN(mm)) return `${h}:${m}`;
    const ampm = hh < 12 ? 'AM' : 'PM';
    const h12 = hh % 12 === 0 ? 12 : hh % 12;
    return `${h12}:${String(mm).padStart(2, '0')} ${ampm}`;
  };
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  if (min === '*' && hr === '*') return 'Every minute';
  if (min.startsWith('*/') && hr === '*') return `Every ${min.slice(2)} minutes`;
  if (hr.startsWith('*/') && min !== '*') return `Every ${hr.slice(2)} hours at :${min.padStart(2, '0')}`;
  if (dom === '*' && dow === '*') return `Every day at ${at(hr, min)}`;
  if (dow !== '*' && dom === '*') {
    const label = dow.split(',').map((d) => days[Number(d)] ?? d).join(', ');
    return `Every ${label} at ${at(hr, min)}`;
  }
  if (dom !== '*' && dow === '*') return `Day ${dom} of each month at ${at(hr, min)}`;
  return `At ${at(hr, min)}`;
}

const CRON_PRESETS: { label: string; value: string }[] = [
  { label: 'Every 15 minutes', value: '*/15 * * * *' },
  { label: 'Hourly', value: '0 * * * *' },
  { label: 'Daily at 9 AM', value: '0 9 * * *' },
  { label: 'Weekdays at 8 AM', value: '0 8 * * 1-5' },
  { label: 'Weekly (Mon 9 AM)', value: '0 9 * * 1' },
  { label: 'Monthly (1st, 2 AM)', value: '0 2 1 * *' },
];

// ── Type-specific config panels ───────────────────────────────────────────────

function TriggerConfig({ data, onUpdate }: PanelProps) {
  const triggerType = String(data.triggerType ?? 'api');
  const cron = String(data.cron ?? '');
  const human = cron ? humanCron(cron) : '';
  return (
    <>
      <SelectField
        label="How does this workflow start?"
        value={triggerType}
        onChange={(v) => onUpdate({ triggerType: v })}
        options={[
          { label: '▶ Manual / API', value: 'api' },
          { label: '🕑 Schedule (cron)', value: 'schedule' },
          { label: '🔗 Webhook (URL)', value: 'webhook' },
        ]}
      />

      {triggerType === 'schedule' && (
        <>
          <div>
            <span className="block text-xs font-medium text-white/50 mb-1">Presets</span>
            <div className="flex flex-wrap gap-1.5">
              {CRON_PRESETS.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  onClick={() => onUpdate({ cron: p.value })}
                  className={`px-2 py-1 rounded-lg text-xs border transition-colors ${
                    cron === p.value
                      ? 'border-sky-500 bg-sky-500/15 text-sky-300'
                      : 'border-white/10 text-white/50 hover:border-white/25 hover:text-white/80'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <TextField
            label="Cron expression"
            mono
            value={cron}
            onChange={(v) => onUpdate({ cron: v })}
            placeholder="0 9 * * *"
            description="minute hour day-of-month month day-of-week (UTC)"
          />
          {cron && (
            <p className={`text-xs flex items-center gap-1.5 ${human ? 'text-emerald-400' : 'text-amber-400'}`}>
              <Clock className="h-3.5 w-3.5" />
              {human || 'Unrecognized cron — will be validated on publish'}
            </p>
          )}
          <TextField
            label="Timezone"
            value={String(data.timezone ?? 'UTC')}
            onChange={(v) => onUpdate({ timezone: v })}
            placeholder="UTC"
          />
          <p className="text-xs text-white/30 flex items-start gap-1.5">
            <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            Scheduled runs fire with empty inputs. Use a webhook if the workflow
            needs per-run data.
          </p>
        </>
      )}

      {triggerType === 'webhook' && (
        <>
          <TextField
            label="Webhook path"
            mono
            value={String(data.webhook_path ?? '')}
            onChange={(v) => onUpdate({ webhook_path: v })}
            placeholder="/webhooks/my-workflow"
            description="A signed trigger URL is minted on publish; POST its body as the run inputs."
          />
        </>
      )}

      {triggerType === 'api' && (
        <p className="text-xs text-white/30 flex items-start gap-1.5">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          Runs are started manually (Test / Run) or via the API. No schedule.
        </p>
      )}
    </>
  );
}

function LLMConfig({ data, onUpdate }: PanelProps) {
  const temperature = data.temperature as number | undefined;
  return (
    <>
      <TextAreaField
        label="Prompt" mono rows={6}
        value={String(data.prompt ?? '')}
        onChange={(v) => onUpdate({ prompt: v })}
        placeholder="Enter your LLM prompt. Use {{inputs.X}} for dynamic values."
        description="Supports templating: {{inputs.*}}, {{steps.<id>.output.*}}."
      />
      <SelectField
        label="Model"
        value={String(data.model ?? 'gpt-4o')}
        onChange={(v) => onUpdate({ model: v })}
        options={[
          { label: 'GPT-4o', value: 'gpt-4o' },
          { label: 'GPT-4o Mini', value: 'gpt-4o-mini' },
          { label: 'Claude 3.5 Sonnet', value: 'claude-3-5-sonnet-20241022' },
          { label: 'Claude 3.5 Haiku', value: 'claude-3-5-haiku-20241022' },
          { label: 'Gemini 1.5 Pro', value: 'gemini-1.5-pro' },
        ]}
      />
      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="Temperature"
          value={temperature}
          onChange={(v) => onUpdate({ temperature: v })}
          placeholder="0.7"
          min={0} max={2} step={0.1}
          description="0 = deterministic"
        />
        <NumberField
          label="Max tokens"
          value={data.max_tokens as number | undefined ?? 2000}
          onChange={(v) => onUpdate({ max_tokens: v ?? 2000 })}
          placeholder="2000"
          min={1} step={100}
        />
      </div>
      <ToggleField
        label="JSON output"
        value={Boolean(data.json_output)}
        onChange={(v) => onUpdate({ json_output: v })}
        description="Force the model to return a valid JSON object."
      />
    </>
  );
}

function ToolConfig({ data, onUpdate, nodeId }: PanelProps) {
  return (
    <>
      <TextField
        label="Tool name"
        value={String(data.tool ?? '')}
        onChange={(v) => onUpdate({ tool: v })}
        placeholder="e.g. github.create_issue"
        description="MCP tool identifier (namespace.action)"
      />
      <KeyValueEditor
        key={nodeId}
        label="Arguments"
        value={data.input as Record<string, unknown> | undefined}
        onChange={(input) => onUpdate({ input })}
        keyPlaceholder="arg"
        valuePlaceholder='{{inputs.repo}}'
        description="Passed to the tool as its input object. Values may reference run inputs or prior step outputs."
      />
    </>
  );
}

function HTTPConfig({ data, onUpdate, nodeId }: PanelProps) {
  const method = String(data.method ?? 'POST');
  const auth = (data.auth as Record<string, unknown> | undefined) ?? {};
  const authType = String(auth.type ?? 'none');
  const setAuth = (patch: Record<string, unknown>) => onUpdate({ auth: { ...auth, ...patch } });
  const hasBody = method !== 'GET';
  return (
    <>
      <TextField
        label="URL"
        value={String(data.url ?? '')}
        onChange={(v) => onUpdate({ url: v })}
        placeholder="https://api.example.com/endpoint"
        description="Supports templating, e.g. https://api.x.com/{{inputs.id}}"
      />
      <SelectField
        label="Method"
        value={method}
        onChange={(v) => onUpdate({ method: v })}
        options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => ({ label: m, value: m }))}
      />
      <KeyValueEditor
        key={`${nodeId}-headers`}
        label="Headers"
        value={data.headers as Record<string, unknown> | undefined}
        onChange={(headers) => onUpdate({ headers })}
        keyPlaceholder="Content-Type"
        valuePlaceholder="application/json"
      />
      <SelectField
        label="Authentication"
        value={authType}
        onChange={(v) => setAuth({ type: v })}
        options={[
          { label: 'None', value: 'none' },
          { label: 'Bearer token', value: 'bearer' },
          { label: 'Basic auth', value: 'basic' },
        ]}
      />
      {authType === 'bearer' && (
        <TextField
          label="Token"
          mono
          value={String(auth.token ?? '')}
          onChange={(v) => setAuth({ token: v })}
          placeholder="{{inputs.api_token}}"
          description="Sent as Authorization: Bearer <token>"
        />
      )}
      {authType === 'basic' && (
        <>
          <TextField
            label="Username"
            value={String(auth.username ?? '')}
            onChange={(v) => setAuth({ username: v })}
            placeholder="username"
          />
          <TextField
            label="Password"
            value={String(auth.password ?? '')}
            onChange={(v) => setAuth({ password: v })}
            placeholder="{{inputs.password}}"
          />
        </>
      )}
      {hasBody && (
        <KeyValueEditor
          key={`${nodeId}-body`}
          label="Request body"
          value={data.request_body as Record<string, unknown> | undefined}
          onChange={(request_body) => onUpdate({ request_body })}
          keyPlaceholder="field"
          valuePlaceholder='{{inputs.value}}'
          description="Sent as a JSON body. Use Raw JSON for nested structures."
        />
      )}
    </>
  );
}

function HITLConfig({ data, onUpdate }: PanelProps) {
  return (
    <>
      <TextField
        label="Assignee role"
        value={String(data.assignee_role ?? '')}
        onChange={(v) => onUpdate({ assignee_role: v })}
        placeholder="e.g. compliance_officer"
        description="Role or user the approval is routed to."
      />
      <SelectField
        label="Priority"
        value={String(data.priority ?? 'medium')}
        onChange={(v) => onUpdate({ priority: v })}
        options={[
          { label: '🔴 Critical', value: 'critical' },
          { label: '🟠 High', value: 'high' },
          { label: '🟡 Medium', value: 'medium' },
          { label: '⚪ Low', value: 'low' },
        ]}
      />
      <NumberField
        label="Escalate after (hours)"
        value={data.escalation_after_hours as number | undefined ?? 48}
        onChange={(v) => onUpdate({ escalation_after_hours: v ?? 48 })}
        placeholder="48"
        min={0} step={1}
        description="Hours before the request auto-escalates."
      />
      <SelectField
        label="On timeout"
        value={String(data.timeout_action ?? 'escalate')}
        onChange={(v) => onUpdate({ timeout_action: v })}
        options={[
          { label: 'Escalate', value: 'escalate' },
          { label: 'Auto-approve', value: 'approve' },
          { label: 'Auto-reject', value: 'reject' },
        ]}
      />
    </>
  );
}

function ConditionalConfig({ data, onUpdate }: PanelProps) {
  return (
    <>
      <TextField
        label="Expression"
        mono
        value={String(data.expression ?? '')}
        onChange={(v) => onUpdate({ expression: v })}
        placeholder='{{steps.risk.output.score}} > 0.7'
        description="Python-compatible expression. Supports ==, >, <, and, or, in"
      />
      <p className="text-xs text-white/30 flex items-start gap-1.5">
        <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
        When true, flow follows the green (True) handle; otherwise the red
        (False) handle.
      </p>
    </>
  );
}

function ForeachConfig({ data, onUpdate }: PanelProps) {
  return (
    <>
      <TextField
        label="Iterate over"
        mono
        value={String(data.iterate_over ?? '')}
        onChange={(v) => onUpdate({ iterate_over: v })}
        placeholder="{{steps.fetch.output.items}}"
        description="An array from a run input or a prior step's output."
      />
      <TextField
        label="Loop variable"
        value={String(data.as_var ?? 'item')}
        onChange={(v) => onUpdate({ as_var: v })}
        placeholder="item"
        description="Use as {{foreach.item}} in body steps"
      />
      <NumberField
        label="Max concurrency"
        value={data.max_concurrency as number | undefined ?? 5}
        onChange={(v) => onUpdate({ max_concurrency: v ?? 5 })}
        placeholder="5"
        min={1} step={1}
        description="How many iterations run in parallel."
      />
      <TextField
        label="Collect output as"
        value={String(data.collect_output_as ?? '')}
        onChange={(v) => onUpdate({ collect_output_as: v })}
        placeholder="results"
        description="Optional. Name for the array of per-iteration outputs."
      />
    </>
  );
}

function CodeConfig({ data, onUpdate }: PanelProps) {
  const runtime = String(data.runtime ?? 'python');
  return (
    <>
      <SelectField
        label="Runtime"
        value={runtime}
        onChange={(v) => onUpdate({ runtime: v })}
        options={[
          { label: 'Python 3.12', value: 'python' },
          { label: 'JavaScript (Node)', value: 'javascript' },
        ]}
      />
      <TextAreaField
        label="Code"
        mono rows={12}
        value={String(data.code ?? '')}
        onChange={(v) => onUpdate({ code: v })}
        placeholder={
          runtime === 'javascript'
            ? "// Access inputs via: inputs['key']\nreturn { result: (inputs.text || '').toUpperCase() };"
            : "# Access inputs via: inputs['key']\noutput = {'result': inputs.get('text', '').upper()}"
        }
        description={
          runtime === 'javascript'
            ? 'Return an object; it becomes this step’s output.'
            : 'Assign to `output` (a dict); it becomes this step’s output.'
        }
      />
    </>
  );
}

function SetVariableConfig({ data, onUpdate }: PanelProps) {
  return (
    <>
      <TextField
        label="Variable name"
        value={String(data.var_name ?? '')}
        onChange={(v) => onUpdate({ var_name: v })}
        placeholder="my_variable"
        description="Referenced later as {{vars.my_variable}}."
      />
      <TextField
        label="Value expression"
        mono
        value={String(data.var_value ?? '')}
        onChange={(v) => onUpdate({ var_value: v })}
        placeholder="{{steps.llm.output.result}}"
      />
      <SelectField
        label="Type"
        value={String(data.value_type ?? 'string')}
        onChange={(v) => onUpdate({ value_type: v })}
        options={[
          { label: 'String', value: 'string' },
          { label: 'Number', value: 'number' },
          { label: 'Integer', value: 'integer' },
          { label: 'Boolean', value: 'boolean' },
          { label: 'JSON', value: 'json' },
        ]}
      />
    </>
  );
}

function WaitConfig({ data, onUpdate }: PanelProps) {
  return (
    <>
      <TextField
        label="Duration"
        value={String(data.duration ?? '')}
        onChange={(v) => onUpdate({ duration: v })}
        placeholder="e.g. 30s, 5m, 2h"
        description="Fixed delay. Leave empty to wait for an event instead."
      />
      <TextField
        label="Event channel (optional)"
        mono
        value={String(data.event_channel ?? '')}
        onChange={(v) => onUpdate({ event_channel: v })}
        placeholder="e.g. payment.confirmed"
        description="Resumes when a matching event is received."
      />
    </>
  );
}

function EmitEventConfig({ data, onUpdate, nodeId }: PanelProps) {
  return (
    <>
      <TextField
        label="Event channel"
        mono
        value={String(data.event_channel_out ?? '')}
        onChange={(v) => onUpdate({ event_channel_out: v })}
        placeholder="e.g. workflow.step.completed"
      />
      <KeyValueEditor
        key={nodeId}
        label="Payload"
        value={data.event_payload as Record<string, unknown> | undefined}
        onChange={(event_payload) => onUpdate({ event_payload })}
        keyPlaceholder="field"
        valuePlaceholder='{{steps.last.output}}'
        description="Data published with the event."
      />
    </>
  );
}

function OcrConfig({ data, onUpdate }: PanelProps) {
  const input = (data.input as Record<string, unknown> | undefined) ?? {};
  const s = (k: string) => String(input[k] ?? '');
  const setInput = (patch: Record<string, unknown>) => onUpdate({ input: patchDict(data.input, patch) });
  return (
    <>
      <p className="text-xs text-white/30 flex items-start gap-1.5">
        <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
        Provide the document via exactly one source. Base64 fields usually
        reference a run input or a prior step ({'{{inputs.doc}}'}).
      </p>
      <TextField
        label="URL"
        value={s('url')}
        onChange={(v) => setInput({ url: v })}
        placeholder="https://example.com/scan.pdf"
        description="Fetch the document from a URL."
      />
      <TextField
        label="Image (base64 / ref)"
        mono
        value={s('image_base64')}
        onChange={(v) => setInput({ image_base64: v })}
        placeholder="{{inputs.registration_doc}}"
      />
      <TextField
        label="PDF (base64 / ref)"
        mono
        value={s('pdf_base64')}
        onChange={(v) => setInput({ pdf_base64: v })}
        placeholder="{{inputs.invoice_pdf}}"
      />
      <TextField
        label="Document (base64 / ref)"
        mono
        value={s('document_base64')}
        onChange={(v) => setInput({ document_base64: v })}
        placeholder="{{inputs.document}}"
      />
      <TextField
        label="File path"
        mono
        value={s('file_path')}
        onChange={(v) => setInput({ file_path: v })}
        placeholder="/artifacts/uploaded.png"
      />
      <div className="grid grid-cols-2 gap-3">
        <TextField
          label="Content type"
          value={s('content_type')}
          onChange={(v) => setInput({ content_type: v })}
          placeholder="application/pdf"
        />
        <TextField
          label="Filename"
          value={s('filename')}
          onChange={(v) => setInput({ filename: v })}
          placeholder="invoice.pdf"
        />
      </div>
    </>
  );
}

function RpaConfig({ data, onUpdate, nodeId }: PanelProps) {
  const input = (data.input as Record<string, unknown> | undefined) ?? {};
  const s = (k: string) => String(input[k] ?? '');
  const setInput = (patch: Record<string, unknown>) => onUpdate({ input: patchDict(data.input, patch) });
  return (
    <>
      <TextField
        label="URL"
        value={s('url')}
        onChange={(v) => setInput({ url: v })}
        placeholder="{{inputs.website_url}}"
        description="Page to open. Internal/loopback hosts are blocked (SSRF guard)."
      />
      <TextField
        label="Title (optional)"
        value={s('title')}
        onChange={(v) => setInput({ title: v })}
        placeholder="Report title"
      />
      <KeyValueEditor
        key={`${nodeId}-selectors`}
        label="Selectors"
        value={input.selectors as Record<string, unknown> | undefined}
        onChange={(selectors) => setInput({ selectors })}
        keyPlaceholder="name"
        valuePlaceholder=".css-selector"
        description="Named CSS selectors to extract from the page (name → selector)."
      />
      <ToggleField
        label="Generate PDF"
        value={Boolean(input.generate_pdf)}
        onChange={(v) => setInput({ generate_pdf: v })}
        description="Render the page to a PDF artifact."
      />
      <ToggleField
        label="Allow HTTP fetch"
        value={Boolean(input.allow_http_fetch)}
        onChange={(v) => setInput({ allow_http_fetch: v })}
        description="Permit plain-HTTP (non-HTTPS) fetches."
      />
    </>
  );
}

function ParallelConfig() {
  return (
    <p className="text-xs text-white/40 flex items-start gap-1.5">
      <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      Runs its branch steps concurrently and merges their outputs. Branch steps
      are authored in the YAML editor (nested <code className="text-white/60">parallel_branches</code>).
    </p>
  );
}

function RagConfig({ data, onUpdate }: PanelProps) {
  const rag = (data.rag as Record<string, unknown> | undefined) ?? {};
  const input = (data.input as Record<string, unknown> | undefined) ?? {};
  const setRag = (patch: Record<string, unknown>) => onUpdate({ rag: { ...rag, ...patch } });
  const setInput = (patch: Record<string, unknown>) => onUpdate({ input: patchDict(data.input, patch) });
  return (
    <>
      <TextAreaField
        label="Prompt / query"
        mono rows={5}
        value={String(data.prompt ?? '')}
        onChange={(v) => onUpdate({ prompt: v })}
        placeholder="Answer using retrieved context: {{inputs.question}}"
      />
      <TextField
        label="Collection"
        value={String(input.collection ?? rag.collection ?? '')}
        onChange={(v) => setInput({ collection: v })}
        placeholder="knowledge collection name"
        description="Knowledge collection to retrieve from."
      />
      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="Top K"
          value={rag.top_k as number | undefined ?? 5}
          onChange={(v) => setRag({ top_k: v ?? 5 })}
          placeholder="5"
          min={1} step={1}
        />
        <SelectField
          label="Strategy"
          value={String(rag.strategy ?? 'hybrid')}
          onChange={(v) => setRag({ strategy: v })}
          options={[
            { label: 'Hybrid', value: 'hybrid' },
            { label: 'Semantic', value: 'semantic' },
            { label: 'Keyword', value: 'keyword' },
          ]}
        />
      </div>
    </>
  );
}

function SubWorkflowConfig({ data, onUpdate, nodeId }: PanelProps) {
  return (
    <>
      <TextField
        label="Workflow ID"
        mono
        value={String(data.workflow_id ?? '')}
        onChange={(v) => onUpdate({ workflow_id: v })}
        placeholder="uuid of the workflow to call"
      />
      <KeyValueEditor
        key={nodeId}
        label="Inputs"
        value={data.workflow_inputs as Record<string, unknown> | undefined}
        onChange={(workflow_inputs) => onUpdate({ workflow_inputs })}
        keyPlaceholder="merchant_id"
        valuePlaceholder='{{inputs.merchant_id}}'
        description="Values passed to the called workflow as its run inputs."
      />
    </>
  );
}

// ── Config router ─────────────────────────────────────────────────────────────

const TYPE_CONFIGS: Record<string, (p: PanelProps) => React.ReactNode> = {
  trigger:      TriggerConfig,
  llm:          LLMConfig,
  ocr:          OcrConfig,
  rpa:          RpaConfig,
  parallel:     ParallelConfig,
  rag:          RagConfig,
  sub_workflow: SubWorkflowConfig,
  tool:         ToolConfig,
  http:         HTTPConfig,
  hitl:         HITLConfig,
  conditional:  ConditionalConfig,
  foreach:      ForeachConfig,
  code:         CodeConfig,
  set_variable: SetVariableConfig,
  wait:         WaitConfig,
  emit_event:   EmitEventConfig,
};

// ── Common "Advanced" panel (timeout / retry / on-failure) ────────────────────

function AdvancedConfig({ data, onUpdate }: PanelProps) {
  const retry = (data.retry as Record<string, unknown> | undefined) ?? {};
  const setRetry = (patch: Record<string, unknown>) =>
    onUpdate({ retry: { ...retry, ...patch } });
  const onFailure = String(data.on_failure ?? 'abort');
  return (
    <CollapsibleSection title="Advanced" icon={<ShieldAlert className="h-3.5 w-3.5" />}>
      <TextField
        label="Timeout"
        value={String(data.timeout ?? '')}
        onChange={(v) => onUpdate({ timeout: v })}
        placeholder="e.g. 60s, 5m"
        description="Max wall-clock time for this step before it fails."
      />
      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="Retry attempts"
          value={retry.max_attempts as number | undefined}
          onChange={(v) => setRetry({ max_attempts: v })}
          placeholder="0"
          min={0} step={1}
        />
        <NumberField
          label="Backoff (seconds)"
          value={retry.backoff_seconds as number | undefined}
          onChange={(v) => setRetry({ backoff_seconds: v })}
          placeholder="2"
          min={0} step={1}
        />
      </div>
      <SelectField
        label="On failure"
        value={onFailure}
        onChange={(v) => onUpdate({ on_failure: v })}
        options={[
          { label: 'Abort the run', value: 'abort' },
          { label: 'Skip this step', value: 'skip' },
          { label: 'Pause for review', value: 'pause' },
          { label: 'Use a default value', value: 'use_default' },
        ]}
      />
      {onFailure === 'use_default' && (
        <TextAreaField
          label="Default value"
          mono rows={3}
          value={String(data.on_failure_default ?? '')}
          onChange={(v) => onUpdate({ on_failure_default: v })}
          placeholder='{"status": "unknown"}'
          description="Output substituted for this step when it fails."
        />
      )}
    </CollapsibleSection>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function WorkflowStepConfig({ node, onUpdate, onClose }: StepConfigProps) {
  const data = node.data as WorkflowNodeData;
  const stepType = data.stepType ?? node.type ?? 'tool';
  const icon = NODE_ICONS[stepType] ?? '◻';
  const label = NODE_LABELS[stepType] ?? stepType;
  const TypeConfig = TYPE_CONFIGS[stepType];

  return (
    <div className="flex flex-col h-full overflow-y-auto" role="complementary" aria-label="Step configuration">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 shrink-0">
        <span className="flex items-center gap-2 text-sm font-semibold text-white">
          <span aria-hidden>{icon}</span>
          {label}
        </span>
        <motion.button
          whileHover={{ scale: 1.15, rotate: 90 }}
          whileTap={{ scale: 0.9 }}
          transition={springs.snappy}
          onClick={onClose}
          className="text-white/40 hover:text-white transition-colors"
          aria-label="Close config panel"
        >
          <X className="h-4 w-4" />
        </motion.button>
      </div>

      {/* Fields */}
      <div className="flex-1 px-4 py-4 space-y-4 overflow-y-auto">
        {/* Common: label */}
        <TextField
          label="Label"
          value={String(data.label ?? '')}
          onChange={(v) => onUpdate({ label: v })}
          placeholder="Descriptive step name"
        />

        {/* Type-specific fields */}
        {TypeConfig && <TypeConfig data={data} onUpdate={onUpdate} nodeId={node.id} />}

        {!TypeConfig && (
          <p className="text-xs text-white/30 flex items-center gap-1.5">
            <Settings className="h-3.5 w-3.5" />
            No additional configuration for this step type.
          </p>
        )}

        {/* Common: retry / timeout / on-failure */}
        <AdvancedConfig data={data} onUpdate={onUpdate} nodeId={node.id} />
      </div>

      {/* Step ID (read-only) */}
      <div className="px-4 py-3 border-t border-white/8 shrink-0">
        <p className="text-xs text-white/25 font-mono">ID: {node.id}</p>
      </div>
    </div>
  );
}
