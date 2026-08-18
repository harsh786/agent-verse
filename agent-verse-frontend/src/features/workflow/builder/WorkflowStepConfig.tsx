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
import { X, Settings } from 'lucide-react';
import type { Node } from '@xyflow/react';
import { NODE_ICONS, NODE_LABELS } from '../design/tokens';
import type { WorkflowNodeData } from './nodes/BaseWorkflowNode';

interface StepConfigProps {
  node: Node;
  onUpdate: (updates: Partial<WorkflowNodeData>) => void;
  onClose: () => void;
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

// ── Type-specific config panels ───────────────────────────────────────────────

function LLMConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <TextField
        label="Prompt" multiline mono
        value={String(data.prompt ?? '')}
        onChange={(v) => onUpdate({ prompt: v })}
        placeholder="Enter your LLM prompt. Use {{inputs.X}} for dynamic values."
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
      <TextField
        label="Max tokens"
        value={String(data.max_tokens ?? '2000')}
        onChange={(v) => onUpdate({ max_tokens: Number(v) || 2000 })}
        placeholder="2000"
      />
    </>
  );
}

function ToolConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <TextField
        label="Tool name"
        value={String(data.tool ?? '')}
        onChange={(v) => onUpdate({ tool: v })}
        placeholder="e.g. github.create_issue"
        description="MCP tool identifier (namespace.action)"
      />
      <TextField
        label="Input mapping (JSON)"
        multiline mono
        value={String(data.input ? JSON.stringify(data.input, null, 2) : '')}
        onChange={(v) => {
          try { onUpdate({ input: JSON.parse(v) }); } catch { /* ignore parse errors */ }
        }}
        placeholder='{"repo": "{{inputs.repo}}", "title": "{{steps.llm.output.result}}"}'
      />
    </>
  );
}

function HTTPConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <TextField
        label="URL"
        value={String(data.url ?? '')}
        onChange={(v) => onUpdate({ url: v })}
        placeholder="https://api.example.com/endpoint"
      />
      <SelectField
        label="Method"
        value={String(data.method ?? 'POST')}
        onChange={(v) => onUpdate({ method: v })}
        options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => ({ label: m, value: m }))}
      />
      <TextField
        label="Request body (JSON)"
        multiline mono
        value={String(data.request_body ? JSON.stringify(data.request_body, null, 2) : '')}
        onChange={(v) => {
          try { onUpdate({ request_body: JSON.parse(v) }); } catch { /* ignore */ }
        }}
        placeholder='{"key": "{{inputs.value}}"}'
      />
    </>
  );
}

function HITLConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <TextField
        label="Assignee role"
        value={String(data.assignee_role ?? '')}
        onChange={(v) => onUpdate({ assignee_role: v })}
        placeholder="e.g. compliance_officer"
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
      <TextField
        label="Timeout"
        value={String(data.escalation_after_hours ?? '48')}
        onChange={(v) => onUpdate({ escalation_after_hours: parseFloat(v) || 48 })}
        placeholder="48"
        description="Hours before auto-escalation"
      />
    </>
  );
}

function ConditionalConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
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
    </>
  );
}

function ForeachConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <TextField
        label="Iterate over"
        mono
        value={String(data.iterate_over ?? '')}
        onChange={(v) => onUpdate({ iterate_over: v })}
        placeholder="{{steps.fetch.output.items}}"
      />
      <TextField
        label="Loop variable"
        value={String(data.as_var ?? 'item')}
        onChange={(v) => onUpdate({ as_var: v })}
        placeholder="item"
        description="Use as {{foreach.item}} in body steps"
      />
      <TextField
        label="Max concurrency"
        value={String(data.max_concurrency ?? '5')}
        onChange={(v) => onUpdate({ max_concurrency: parseInt(v, 10) || 5 })}
        placeholder="5"
      />
    </>
  );
}

function CodeConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <SelectField
        label="Runtime"
        value={String(data.runtime ?? 'python')}
        onChange={(v) => onUpdate({ runtime: v })}
        options={[
          { label: 'Python 3.12', value: 'python' },
          { label: 'JavaScript (Node)', value: 'javascript' },
        ]}
      />
      <TextField
        label="Code"
        multiline mono
        value={String(data.code ?? '')}
        onChange={(v) => onUpdate({ code: v })}
        placeholder="# Access inputs via: inputs['key']\noutput = {'result': inputs.get('text', '').upper()}"
      />
    </>
  );
}

function SetVariableConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <TextField
        label="Variable name"
        value={String(data.var_name ?? '')}
        onChange={(v) => onUpdate({ var_name: v })}
        placeholder="my_variable"
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
        ]}
      />
    </>
  );
}

function WaitConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <TextField
        label="Duration"
        value={String(data.duration ?? '')}
        onChange={(v) => onUpdate({ duration: v })}
        placeholder="e.g. 30s, 5m, 2h"
        description="Leave empty to wait for an event"
      />
      <TextField
        label="Event channel (optional)"
        mono
        value={String(data.event_channel ?? '')}
        onChange={(v) => onUpdate({ event_channel: v })}
        placeholder="e.g. payment.confirmed"
      />
    </>
  );
}

function EmitEventConfig({ data, onUpdate }: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) {
  return (
    <>
      <TextField
        label="Event channel"
        mono
        value={String(data.event_channel_out ?? '')}
        onChange={(v) => onUpdate({ event_channel_out: v })}
        placeholder="e.g. workflow.step.completed"
      />
      <TextField
        label="Payload (JSON)"
        multiline mono
        value={String(data.event_payload ? JSON.stringify(data.event_payload, null, 2) : '')}
        onChange={(v) => {
          try { onUpdate({ event_payload: JSON.parse(v) }); } catch { /* ignore */ }
        }}
        placeholder='{"result": "{{steps.last.output}}"}'
      />
    </>
  );
}

// ── Config router ─────────────────────────────────────────────────────────────

const TYPE_CONFIGS: Record<string, (p: { data: WorkflowNodeData; onUpdate: (u: Partial<WorkflowNodeData>) => void }) => React.ReactNode> = {
  llm:          LLMConfig,
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
        {TypeConfig && <TypeConfig data={data} onUpdate={onUpdate} />}

        {!TypeConfig && (
          <p className="text-xs text-white/30 flex items-center gap-1.5">
            <Settings className="h-3.5 w-3.5" />
            No additional configuration for this step type.
          </p>
        )}
      </div>

      {/* Step ID (read-only) */}
      <div className="px-4 py-3 border-t border-white/8 shrink-0">
        <p className="text-xs text-white/25 font-mono">ID: {node.id}</p>
      </div>
    </div>
  );
}
