/**
 * World-class visual workflow builder using @xyflow/react.
 * Features: drag-drop, type-specific inspector, undo/redo, NL generation,
 * templates, validation, live run animation.
 */
import { useState, useCallback, useRef, useEffect, DragEvent } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap, BackgroundVariant,
  ReactFlowProvider, addEdge, useNodesState, useEdgesState,
  useReactFlow, ConnectionMode,
  type Node, type Edge, type Connection,
  MarkerType, Handle, Position,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/auth';
import { toast } from '../../stores/toast';
import { workflowsApi } from '../../lib/api/client';

// ─── Node Types ──────────────────────────────────────────────────────────────

const NODE_COLORS: Record<string, string> = {
  trigger:        'bg-green-100 border-green-400 text-green-800',
  tool_call:      'bg-blue-100 border-blue-400 text-blue-800',
  agent_step:     'bg-purple-100 border-purple-400 text-purple-800',
  decision:       'bg-yellow-100 border-yellow-400 text-yellow-800',
  parallel:       'bg-orange-100 border-orange-400 text-orange-800',
  loop:           'bg-cyan-100 border-cyan-400 text-cyan-800',
  human_approval: 'bg-red-100 border-red-400 text-red-800',
  delay:          'bg-slate-100 border-slate-400 text-slate-700',
  end:            'bg-muted/60 border-muted-foreground/50 text-gray-900 dark:text-gray-100',
};

const NODE_ICONS: Record<string, string> = {
  trigger: '▶', tool_call: '🔧', agent_step: '🤖', decision: '❓',
  parallel: '⫸', loop: '↻', human_approval: '👤', delay: '⏱', end: '⬛',
};

interface WorkflowNodeData {
  type: string;
  label: string;
  subtitle?: string;
  status?: string | null;
  description?: string;
  // trigger
  trigger_type?: string;
  cron_expression?: string;
  // tool_call
  tool?: string;
  output_variable?: string;
  input_mapping?: string;
  // agent_step
  agent_id_cfg?: string;
  goal_template?: string;
  // decision
  condition?: string;
  true_label?: string;
  false_label?: string;
  // parallel
  max_concurrency?: number;
  // loop
  iterator?: string;
  max_iterations?: number;
  break_condition?: string;
  // human_approval
  approval_message?: string;
  approvers?: string;
  timeout_minutes?: number;
  // delay
  duration?: number;
  duration_unit?: string;
  // end
  output_mapping?: string;
  [key: string]: unknown;
}

function WorkflowNode({ data, selected }: { data: WorkflowNodeData; selected?: boolean }) {
  const color = NODE_COLORS[data.type] ?? 'bg-muted border-border';
  const hasValidationError = data.type === 'tool_call' && !data.tool;
  return (
    <div
      className={`rounded-lg border-2 p-3 min-w-[140px] shadow-sm text-xs ${color} ${
        selected ? 'ring-2 ring-blue-500 ring-offset-1' : ''
      } ${
        data.status === 'running' ? 'animate-pulse ring-2 ring-blue-400' :
        data.status === 'complete' ? '!bg-green-100 !border-green-500' :
        data.status === 'failed' ? '!bg-red-100 !border-red-500' : ''
      }`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-slate-400 !border-slate-600 !w-4 !h-4"
      />
      <div className="flex items-center gap-1.5 font-semibold mb-0.5">
        <span>{NODE_ICONS[data.type] ?? '◻'}</span>
        <span className="truncate">{String(data.label)}</span>
        {hasValidationError && (
          <span className="ml-auto text-red-500 text-[9px] font-bold" title="Tool not configured">⚠</span>
        )}
      </div>
      {data.subtitle && (
        <div className="text-[10px] opacity-60 truncate">{String(data.subtitle)}</div>
      )}
      {data.status && (
        <div
          className={`mt-1 text-[10px] font-medium ${
            data.status === 'running'  ? 'text-blue-600'  :
            data.status === 'complete' ? 'text-green-600' :
            data.status === 'failed'   ? 'text-red-600'   : 'opacity-50'
          }`}
        >
          ● {data.status}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-slate-400 !border-slate-600 !w-4 !h-4"
      />
    </div>
  );
}

const NODE_TYPES = { workflow: WorkflowNode };
export const SNAP_GRID: [number, number] = [16, 16]; // module-level constant — prevents ReactFlow useEffect loop

const CONNECTION_LINE_STYLE: React.CSSProperties = {
  stroke: '#6366f1',
  strokeWidth: 2,
  strokeDasharray: '5 5',
};

// ─── Palette ─────────────────────────────────────────────────────────────────

const PALETTE_NODES = [
  { type: 'trigger',        label: 'Trigger / Start'   },
  { type: 'tool_call',      label: 'Tool Call'          },
  { type: 'agent_step',     label: 'Agent Step'         },
  { type: 'decision',       label: 'Decision / Branch'  },
  { type: 'parallel',       label: 'Parallel Fan-out'   },
  { type: 'loop',           label: 'Loop / Map'         },
  { type: 'human_approval', label: 'Human Approval'     },
  { type: 'delay',          label: 'Delay / Wait'       },
  { type: 'end',            label: 'End'                },
];

// ─── Templates ───────────────────────────────────────────────────────────────

const WORKFLOW_TEMPLATES = [
  {
    id: 'incident-response',
    name: 'Incident Response',
    description: 'Datadog → Jira → Slack',
    nodes: [
      { id: 'trigger', type: 'workflow' as const, position: { x: 250, y: 50 }, data: { type: 'trigger', label: 'Datadog Alert', subtitle: 'webhook', trigger_type: 'webhook', status: null } },
      { id: 's1', type: 'workflow' as const, position: { x: 250, y: 170 }, data: { type: 'agent_step', label: 'Classify Incident', subtitle: 'severity', status: null } },
      { id: 's2', type: 'workflow' as const, position: { x: 250, y: 290 }, data: { type: 'tool_call', label: 'Create Jira Issue', subtitle: 'jira', tool: 'jira_create_issue', status: null } },
      { id: 's3', type: 'workflow' as const, position: { x: 250, y: 410 }, data: { type: 'tool_call', label: 'Notify Slack', subtitle: 'slack', tool: 'slack_send_message', status: null } },
      { id: 'end', type: 'workflow' as const, position: { x: 250, y: 530 }, data: { type: 'end', label: 'End', status: null } },
    ],
    edges: [
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 's3', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e4', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
    ],
  },
  {
    id: 'pr-review',
    name: 'PR Review',
    description: 'GitHub webhook → Agent → PR comment',
    nodes: [
      { id: 'trigger', type: 'workflow' as const, position: { x: 250, y: 50 }, data: { type: 'trigger', label: 'GitHub PR', subtitle: 'webhook', trigger_type: 'webhook', status: null } },
      { id: 's1', type: 'workflow' as const, position: { x: 250, y: 170 }, data: { type: 'agent_step', label: 'Review Code', subtitle: 'analyze PR diff', status: null } },
      { id: 's2', type: 'workflow' as const, position: { x: 250, y: 290 }, data: { type: 'tool_call', label: 'Post PR Comment', subtitle: 'github', tool: 'github_create_comment', status: null } },
      { id: 'end', type: 'workflow' as const, position: { x: 250, y: 410 }, data: { type: 'end', label: 'End', status: null } },
    ],
    edges: [
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
    ],
  },
  {
    id: 'daily-report',
    name: 'Daily Report',
    description: 'Cron → Analytics → Slack',
    nodes: [
      { id: 'trigger', type: 'workflow' as const, position: { x: 250, y: 50 }, data: { type: 'trigger', label: 'Daily Cron', subtitle: '0 9 * * 1-5', trigger_type: 'cron', cron_expression: '0 9 * * 1-5', status: null } },
      { id: 's1', type: 'workflow' as const, position: { x: 250, y: 170 }, data: { type: 'tool_call', label: 'Fetch Analytics', subtitle: 'analytics', tool: 'analytics_summary', status: null } },
      { id: 's2', type: 'workflow' as const, position: { x: 250, y: 290 }, data: { type: 'agent_step', label: 'Generate Report', subtitle: 'format data', status: null } },
      { id: 's3', type: 'workflow' as const, position: { x: 250, y: 410 }, data: { type: 'tool_call', label: 'Send to Slack', subtitle: 'slack', tool: 'slack_send_message', status: null } },
      { id: 'end', type: 'workflow' as const, position: { x: 250, y: 530 }, data: { type: 'end', label: 'End', status: null } },
    ],
    edges: [
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 's3', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e4', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
    ],
  },
  {
    id: 'bug-triage',
    name: 'Bug Triage',
    description: 'Jira webhook → Classify → Assign',
    nodes: [
      { id: 'trigger', type: 'workflow' as const, position: { x: 250, y: 50 }, data: { type: 'trigger', label: 'Jira Issue', subtitle: 'webhook', trigger_type: 'webhook', status: null } },
      { id: 's1', type: 'workflow' as const, position: { x: 250, y: 170 }, data: { type: 'agent_step', label: 'Classify Bug', subtitle: 'severity & type', status: null } },
      { id: 's2', type: 'workflow' as const, position: { x: 250, y: 290 }, data: { type: 'decision', label: 'Critical?', condition: 'severity == "critical"', true_label: 'Yes', false_label: 'No', status: null } },
      { id: 's3', type: 'workflow' as const, position: { x: 100, y: 410 }, data: { type: 'tool_call', label: 'PagerDuty Alert', tool: 'pagerduty_create', status: null } },
      { id: 's4', type: 'workflow' as const, position: { x: 400, y: 410 }, data: { type: 'tool_call', label: 'Assign to Queue', tool: 'jira_assign', status: null } },
      { id: 'end', type: 'workflow' as const, position: { x: 250, y: 530 }, data: { type: 'end', label: 'End', status: null } },
    ],
    edges: [
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 's3', label: 'Yes', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#ef4444', strokeWidth: 2 } },
      { id: 'e4', source: 's2', target: 's4', label: 'No', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e5', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e6', source: 's4', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
    ],
  },
  {
    id: 'onboarding',
    name: 'Employee Onboarding',
    description: 'HR event → Okta → Slack → Notion',
    nodes: [
      { id: 'trigger', type: 'workflow' as const, position: { x: 250, y: 50 }, data: { type: 'trigger', label: 'HR New Hire', subtitle: 'event', trigger_type: 'event', status: null } },
      { id: 's1', type: 'workflow' as const, position: { x: 250, y: 170 }, data: { type: 'tool_call', label: 'Create Okta Account', tool: 'okta_create_user', status: null } },
      { id: 's2', type: 'workflow' as const, position: { x: 100, y: 290 }, data: { type: 'tool_call', label: 'Send Welcome Slack', tool: 'slack_send_message', status: null } },
      { id: 's3', type: 'workflow' as const, position: { x: 400, y: 290 }, data: { type: 'tool_call', label: 'Create Notion Page', tool: 'notion_create_page', status: null } },
      { id: 'end', type: 'workflow' as const, position: { x: 250, y: 410 }, data: { type: 'end', label: 'End', status: null } },
    ],
    edges: [
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e3', source: 's1', target: 's3', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e4', source: 's2', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e5', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
    ],
  },
  {
    id: 'cost-alert',
    name: 'Cost Alert',
    description: 'Cron → Cost check → Alert if over',
    nodes: [
      { id: 'trigger', type: 'workflow' as const, position: { x: 250, y: 50 }, data: { type: 'trigger', label: 'Daily Cost Check', subtitle: '0 8 * * *', trigger_type: 'cron', cron_expression: '0 8 * * *', status: null } },
      { id: 's1', type: 'workflow' as const, position: { x: 250, y: 170 }, data: { type: 'tool_call', label: 'Get Cloud Costs', tool: 'aws_cost_explorer', status: null } },
      { id: 's2', type: 'workflow' as const, position: { x: 250, y: 290 }, data: { type: 'decision', label: 'Over Budget?', condition: 'cost > budget', true_label: 'Over', false_label: 'OK', status: null } },
      { id: 's3', type: 'workflow' as const, position: { x: 250, y: 410 }, data: { type: 'tool_call', label: 'Send Alert', tool: 'slack_send_message', status: null } },
      { id: 'end', type: 'workflow' as const, position: { x: 250, y: 530 }, data: { type: 'end', label: 'End', status: null } },
    ],
    edges: [
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 's3', label: 'Over', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#ef4444', strokeWidth: 2 } },
      { id: 'e4', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#6366f1', strokeWidth: 2 } },
      { id: 'e5', source: 's2', target: 'end', label: 'OK', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#22c55e', strokeWidth: 2 } },
    ],
  },
];

// ─── Type-Specific Inspector ──────────────────────────────────────────────────

function TypeSpecificConfig({
  nodeData,
  onChange,
}: {
  nodeData: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const type = nodeData.type;
  const field = (
    id: string,
    label: string,
    value: string | number | undefined,
    handler: (v: string) => void,
    placeholder?: string,
    inputType: 'text' | 'number' | 'textarea' = 'text',
  ) => (
    <div key={id}>
      <label htmlFor={id} className="text-muted-foreground block mb-1">{label}</label>
      {inputType === 'textarea' ? (
        <textarea
          id={id}
          value={String(value ?? '')}
          onChange={(e) => handler(e.target.value)}
          placeholder={placeholder}
          rows={2}
          className="w-full border rounded px-2 py-1 resize-none bg-background text-xs"
        />
      ) : (
        <input
          id={id}
          type={inputType}
          value={value != null ? String(value) : ''}
          onChange={(e) => handler(e.target.value)}
          placeholder={placeholder}
          className="w-full border rounded px-2 py-1 bg-background text-xs"
        />
      )}
    </div>
  );

  if (type === 'trigger') return (
    <div className="space-y-2">
      <div>
        <label htmlFor="trigger-type" className="text-muted-foreground block mb-1">Trigger Type</label>
        <select
          id="trigger-type"
          value={nodeData.trigger_type ?? 'manual'}
          onChange={(e) => onChange({ trigger_type: e.target.value })}
          className="w-full border rounded px-2 py-1 bg-background text-xs"
        >
          <option value="manual">Manual</option>
          <option value="cron">CRON Schedule</option>
          <option value="webhook">Webhook</option>
          <option value="event">Event</option>
        </select>
      </div>
      {nodeData.trigger_type === 'cron' &&
        field('cron-expr', 'CRON Expression', nodeData.cron_expression, (v) => onChange({ cron_expression: v }), '0 9 * * 1-5')
      }
    </div>
  );

  if (type === 'tool_call') return (
    <div className="space-y-2">
      <div>
        <label htmlFor="tool-selector" className="text-muted-foreground block mb-1">Tool</label>
        <input
          id="tool-selector"
          aria-label="Tool selector"
          value={nodeData.tool ?? ''}
          onChange={(e) => onChange({ tool: e.target.value })}
          placeholder="tool_name (e.g. jira_create_issue)"
          className="w-full border rounded px-2 py-1 bg-background text-xs font-mono"
        />
      </div>
      {field('output-var', 'Output Variable', nodeData.output_variable, (v) => onChange({ output_variable: v }), 'result')}
      {field('input-map', 'Input Mapping (JSON)', nodeData.input_mapping, (v) => onChange({ input_mapping: v }), '{"key": "{{var}}"}', 'textarea')}
    </div>
  );

  if (type === 'agent_step') return (
    <div className="space-y-2">
      {field('agent-id-cfg', 'Agent ID', nodeData.agent_id_cfg, (v) => onChange({ agent_id_cfg: v }), 'optional agent uuid')}
      {field('goal-tmpl', 'Goal Template', nodeData.goal_template, (v) => onChange({ goal_template: v }), 'Analyze {{input}} and return…', 'textarea')}
    </div>
  );

  if (type === 'decision') return (
    <div className="space-y-2">
      {field('condition', 'Condition Expression', nodeData.condition, (v) => onChange({ condition: v }), 'result.status == "ok"', 'textarea')}
      {field('true-label', 'True Branch Label', nodeData.true_label, (v) => onChange({ true_label: v }), 'Yes')}
      {field('false-label', 'False Branch Label', nodeData.false_label, (v) => onChange({ false_label: v }), 'No')}
    </div>
  );

  if (type === 'parallel') return (
    <div className="space-y-2">
      <div>
        <label htmlFor="max-conc" className="text-muted-foreground block mb-1">Max Concurrency</label>
        <input
          id="max-conc"
          type="number"
          min={1} max={20}
          value={nodeData.max_concurrency ?? 5}
          onChange={(e) => onChange({ max_concurrency: parseInt(e.target.value, 10) })}
          className="w-full border rounded px-2 py-1 bg-background text-xs"
        />
      </div>
    </div>
  );

  if (type === 'loop') return (
    <div className="space-y-2">
      {field('iterator', 'Iterator Expression', nodeData.iterator, (v) => onChange({ iterator: v }), 'item in items')}
      <div>
        <label htmlFor="max-iter" className="text-muted-foreground block mb-1">Max Iterations</label>
        <input
          id="max-iter"
          type="number"
          min={1} max={1000}
          value={nodeData.max_iterations ?? 100}
          onChange={(e) => onChange({ max_iterations: parseInt(e.target.value, 10) })}
          className="w-full border rounded px-2 py-1 bg-background text-xs"
        />
      </div>
      {field('break-cond', 'Break Condition', nodeData.break_condition, (v) => onChange({ break_condition: v }), 'item.done == true')}
    </div>
  );

  if (type === 'human_approval') return (
    <div className="space-y-2">
      {field('approval-msg', 'Approval Message', nodeData.approval_message, (v) => onChange({ approval_message: v }), 'Please review and approve…', 'textarea')}
      {field('approvers', 'Approvers (comma-separated emails)', nodeData.approvers, (v) => onChange({ approvers: v }), 'user@example.com')}
      <div>
        <label htmlFor="timeout-min" className="text-muted-foreground block mb-1">Timeout (minutes)</label>
        <input
          id="timeout-min"
          type="number"
          min={1}
          value={nodeData.timeout_minutes ?? 60}
          onChange={(e) => onChange({ timeout_minutes: parseInt(e.target.value, 10) })}
          className="w-full border rounded px-2 py-1 bg-background text-xs"
        />
      </div>
    </div>
  );

  if (type === 'delay') return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="delay-dur" className="text-muted-foreground block mb-1">Duration</label>
          <input
            id="delay-dur"
            type="number"
            min={1}
            value={nodeData.duration ?? 1}
            onChange={(e) => onChange({ duration: parseInt(e.target.value, 10) })}
            className="w-full border rounded px-2 py-1 bg-background text-xs"
          />
        </div>
        <div>
          <label htmlFor="delay-unit" className="text-muted-foreground block mb-1">Unit</label>
          <select
            id="delay-unit"
            value={nodeData.duration_unit ?? 'minutes'}
            onChange={(e) => onChange({ duration_unit: e.target.value })}
            className="w-full border rounded px-2 py-1 bg-background text-xs"
          >
            <option value="seconds">Seconds</option>
            <option value="minutes">Minutes</option>
            <option value="hours">Hours</option>
            <option value="days">Days</option>
          </select>
        </div>
      </div>
    </div>
  );

  if (type === 'end') return (
    <div className="space-y-2">
      {field('output-map', 'Output Mapping (JSON)', nodeData.output_mapping, (v) => onChange({ output_mapping: v }), '{"result": "{{last_output}}"}', 'textarea')}
    </div>
  );

  return null;
}

// ─── Inner Component (needs ReactFlowProvider context) ────────────────────────

function WorkflowBuilderInner() {
  const apiKey = useAuthStore((s) => s.apiKey); // primitive return avoids new-object-per-render re-render loop
  const qc = useQueryClient();
  const { screenToFlowPosition, fitView } = useReactFlow();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [workflowName, setWorkflowName] = useState('My Workflow');
  const [currentWfId, setCurrentWfId] = useState<string | null>(null);
  const [nlGoal, setNlGoal] = useState('');
  const [generating, setGenerating] = useState(false);
  const [running, setRunning] = useState(false);
  const [runOutput, setRunOutput] = useState<string>('');
  const [showTemplates, setShowTemplates] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [showValidation, setShowValidation] = useState(false);
  const nodeCounter = useRef(1);
  // Clipboard: stores a shallow copy of the last-copied node for Ctrl+C / Ctrl+V
  const clipboardNode = useRef<Node | null>(null);
  // Undo/redo history stack
  const historyStack = useRef<{ nodes: Node[]; edges: Edge[] }[]>([{ nodes: [], edges: [] }]);
  const historyIdx = useRef(0);

  const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
  void API_BASE; // referenced below in save function headers

  const { data: savedWorkflows } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => workflowsApi.list(),
    enabled: !!apiKey,
  });

  // ── History helpers ───────────────────────────────────────────────────────

  const pushHistory = useCallback((ns: Node[], es: Edge[]) => {
    historyStack.current = historyStack.current.slice(0, historyIdx.current + 1);
    historyStack.current.push({ nodes: ns, edges: es });
    historyIdx.current = historyStack.current.length - 1;
  }, []);

  const undo = useCallback(() => {
    if (historyIdx.current > 0) {
      historyIdx.current--;
      const snapshot = historyStack.current[historyIdx.current];
      setNodes(snapshot.nodes);
      setEdges(snapshot.edges);
      setSelectedNode(null);
    }
  }, [setNodes, setEdges]);

  const redo = useCallback(() => {
    if (historyIdx.current < historyStack.current.length - 1) {
      historyIdx.current++;
      const snapshot = historyStack.current[historyIdx.current];
      setNodes(snapshot.nodes);
      setEdges(snapshot.edges);
      setSelectedNode(null);
    }
  }, [setNodes, setEdges]);

  // ── Validation ────────────────────────────────────────────────────────────

  const validate = useCallback((ns: Node[]): string[] => {
    const errors: string[] = [];
    const hasTrigger = ns.some((n) => (n.data as WorkflowNodeData).type === 'trigger');
    if (!hasTrigger) errors.push('Workflow must have at least one Trigger node');
    if (ns.length > 1) {
      const connected = new Set<string>();
      edges.forEach((e) => { connected.add(e.source); connected.add(e.target); });
      const isolated = ns.filter((n) => !connected.has(n.id));
      if (isolated.length > 0) errors.push(`${isolated.length} node(s) are isolated (not connected)`);
    }
    return errors;
  }, [edges]);

  // ── Node helpers ──────────────────────────────────────────────────────────

  const addNode = useCallback((type: string, label: string, position?: { x: number; y: number }) => {
    const id = `node_${nodeCounter.current++}`;
    setNodes((nds) => {
      const newNodes = [
        ...nds,
        {
          id, type: 'workflow',
          position: position ?? { x: 200 + Math.random() * 200, y: 100 + Math.random() * 200 },
          data: { type, label, subtitle: '', status: null } satisfies WorkflowNodeData,
        },
      ];
      pushHistory(newNodes, edges);
      return newNodes;
    });
  }, [setNodes, pushHistory, edges]);

  const updateSelectedNodeData = useCallback((patch: Partial<WorkflowNodeData>) => {
    if (!selectedNode) return;
    setNodes((nds) => {
      const updated = nds.map((n) =>
        n.id === selectedNode.id ? { ...n, data: { ...n.data, ...patch } } : n
      );
      pushHistory(updated, edges);
      return updated;
    });
    setSelectedNode((prev) => prev ? { ...prev, data: { ...prev.data, ...patch } } : null);
  }, [selectedNode, setNodes, pushHistory, edges]);

  const onConnect = useCallback((connection: Connection) => {
    setEdges((eds) => {
      const newEdges = addEdge({
        ...connection,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: '#6366f1', strokeWidth: 2 },
      }, eds);
      pushHistory(nodes, newEdges);
      return newEdges;
    });
  }, [setEdges, pushHistory, nodes]);

  const isValidConnection = useCallback((connection: Edge | Connection) => {
    return connection.source !== connection.target;
  }, []);

  // ── Keyboard shortcuts ────────────────────────────────────────────────────

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable
      ) return;

      const isMod = e.ctrlKey || e.metaKey;

      if (isMod && e.key.toLowerCase() === 'z' && !e.shiftKey) { e.preventDefault(); undo(); return; }
      if (isMod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) { e.preventDefault(); redo(); return; }
      if (isMod && e.key === '0') { e.preventDefault(); fitView(); return; }
      if (isMod && e.key.toLowerCase() === 'a') {
        e.preventDefault();
        setNodes((nds) => nds.map((n) => ({ ...n, selected: true })));
        return;
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        setNodes((nds) => {
          const filtered = nds.filter((n) => !n.selected);
          setEdges((eds) => {
            const filteredEdges = eds.filter((e) =>
              !nds.find((n) => n.selected && (n.id === e.source || n.id === e.target))
            );
            pushHistory(filtered, filteredEdges);
            return filteredEdges;
          });
          return filtered;
        });
        setSelectedNode(null);
        return;
      }

      if (isMod && e.key.toLowerCase() === 'c') {
        if (selectedNode) {
          clipboardNode.current = selectedNode;
          toast({ kind: 'success', message: `Copied "${(selectedNode.data as WorkflowNodeData).label}"` });
        }
      }
      if (isMod && e.key.toLowerCase() === 'v') {
        const src = clipboardNode.current;
        if (!src) return;
        const id = `node_${nodeCounter.current++}`;
        const pastedNode: Node = {
          ...src,
          id,
          position: { x: (src.position?.x ?? 200) + 40, y: (src.position?.y ?? 200) + 40 },
          selected: false,
          data: { ...(src.data as WorkflowNodeData) },
        };
        setNodes((nds) => {
          const updated = [...nds, pastedNode];
          pushHistory(updated, edges);
          return updated;
        });
        clipboardNode.current = { ...src, position: pastedNode.position };
        toast({ kind: 'success', message: `Pasted "${(src.data as WorkflowNodeData).label}"` });
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedNode, setNodes, setEdges, undo, redo, fitView, pushHistory, edges]);

  // ── Drag-and-drop handlers ───────────────────────────────────────────────

  const onDragStart = useCallback((event: DragEvent<HTMLButtonElement>, nodeType: string, label: string) => {
    event.dataTransfer.setData('application/workflow-node-type', nodeType);
    event.dataTransfer.setData('application/workflow-node-label', label);
    event.dataTransfer.effectAllowed = 'copy';
  }, []);

  const onDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  }, []);

  const onDrop = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const nodeType = event.dataTransfer.getData('application/workflow-node-type');
    const label = event.dataTransfer.getData('application/workflow-node-label');
    if (!nodeType) return;
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    addNode(nodeType, label, position);
  }, [screenToFlowPosition, addNode]);

  // ── NL generation (calls /workflows/generate) ─────────────────────────────

  const generateFromNL = async () => {
    if (!nlGoal.trim()) return;
    setGenerating(true);
    try {
      const data = await workflowsApi.generate(nlGoal);
      if (data.nodes && data.nodes.length > 0) {
        const newNodes: Node[] = data.nodes.map((n) => ({
          id: n.id,
          type: 'workflow' as const,
          position: n.position,
          data: {
            type: n.type,
            label: n.label,
            subtitle: n.subtitle ?? '',
            status: null,
            tool: n.tool ?? '',
            depends_on: n.depends_on ?? [],
            can_parallel: n.can_parallel ?? true,
          } satisfies WorkflowNodeData,
        }));
        const newEdges: Edge[] = data.edges.map((e) => ({
          id: e.id ?? `e_${e.source}_${e.target}`,
          source: e.source,
          target: e.target,
          markerEnd: { type: MarkerType.ArrowClosed },
          style: { stroke: '#6366f1', strokeWidth: 2 },
        }));
        setNodes(newNodes);
        setEdges(newEdges);
        pushHistory(newNodes, newEdges);
        toast({ kind: 'success', message: `Generated ${data.nodes.length} nodes from your goal` });
      } else {
        toast({ kind: 'error', message: 'No nodes returned — try a more specific goal' });
      }
    } catch {
      toast({ kind: 'error', message: 'Failed to generate workflow' });
    } finally {
      setGenerating(false);
    }
  };

  // ── Save ──────────────────────────────────────────────────────────────────

  const save = async () => {
    const errors = validate(nodes);
    if (errors.length > 0) {
      setValidationErrors(errors);
      setShowValidation(true);
      toast({ kind: 'error', message: `Validation: ${errors[0]}` });
      return;
    }
    const definition = {
      steps: nodes.map((n) => {
        const nodeData = n.data as WorkflowNodeData;
        return { id: n.id, position: n.position, ...nodeData };
      }),
      edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, label: e.label != null ? String(e.label) : undefined })),
    };
    try {
      if (currentWfId) {
        await workflowsApi.update(currentWfId, { name: workflowName, definition });
      } else {
        const data = await workflowsApi.create({ name: workflowName, definition });
        setCurrentWfId(data.id);
      }
      qc.invalidateQueries({ queryKey: ['workflows'] });
      toast({ kind: 'success', message: 'Workflow saved' });
    } catch {
      toast({ kind: 'error', message: 'Failed to save workflow' });
    }
  };

  // ── Run ───────────────────────────────────────────────────────────────────

  const run = async (dryRun = false) => {
    if (!currentWfId) {
      await save();
      if (!currentWfId) {
        toast({ kind: 'info', message: 'Saved — click Run again to execute.' });
        return;
      }
    }
    setRunning(true);
    setRunOutput('');
    // Animate: set all nodes to running
    if (!dryRun) {
      setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, status: 'running' } })));
    }
    try {
      const data = await workflowsApi.run(currentWfId!, dryRun);
      setRunOutput(JSON.stringify(data, null, 2));
      // Animate: set all nodes to complete/dry_run status
      setNodes((nds) => nds.map((n) => ({
        ...n,
        data: { ...n.data, status: data.status === 'complete' ? 'complete' : dryRun ? 'complete' : n.data.status },
      })));
      toast({ kind: 'success', message: dryRun ? 'Dry run complete' : `Workflow ${data.status ?? 'started'}` });
    } catch {
      setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, status: 'failed' } })));
      toast({ kind: 'error', message: 'Run failed' });
    } finally {
      setRunning(false);
    }
  };

  // ── Load workflow ─────────────────────────────────────────────────────────

  const loadWorkflow = async (id: string) => {
    const data = await workflowsApi.get(id);
    setCurrentWfId(id);
    setWorkflowName(data.name);
    const def = (data.definition ?? {}) as {
      steps?: Array<{
        id: string; type: string; label: string; subtitle?: string; description?: string;
        position?: { x: number; y: number }; [k: string]: unknown;
      }>;
      edges?: Array<{ id?: string; source: string; target: string; label?: unknown }>;
    };
    if (def.steps) {
      const loadedNodes = def.steps.map((s) => ({
        id: s.id, type: 'workflow' as const,
        position: s.position ?? { x: 200, y: 200 },
        data: { ...s, type: s.type, label: s.label, subtitle: s.subtitle ?? '', description: s.description ?? '', status: null } satisfies WorkflowNodeData,
      }));
      const loadedEdges: Edge[] = (def.edges ?? []).map((e, i) => ({
        id: e.id ?? `e_${i}`, source: e.source, target: e.target,
        ...(e.label != null ? { label: String(e.label) } : {}),
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { stroke: '#6366f1', strokeWidth: 2 },
      }));
      setNodes(loadedNodes);
      setEdges(loadedEdges);
      pushHistory(loadedNodes, loadedEdges);
    }
  };

  // ── Load template ─────────────────────────────────────────────────────────

  const loadTemplate = (tmpl: typeof WORKFLOW_TEMPLATES[0]) => {
    setNodes(tmpl.nodes as Node[]);
    setEdges(tmpl.edges as Edge[]);
    setWorkflowName(tmpl.name);
    setCurrentWfId(null);
    pushHistory(tmpl.nodes as Node[], tmpl.edges as Edge[]);
    setShowTemplates(false);
    toast({ kind: 'success', message: `Loaded template: ${tmpl.name}` });
  };

  const isEmpty = nodes.length === 0;
  const errors = validationErrors;
  const canUndo = historyIdx.current > 0;
  const canRedo = historyIdx.current < historyStack.current.length - 1;

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b bg-card flex-wrap">
        <input
          value={workflowName}
          onChange={(e) => setWorkflowName(e.target.value)}
          aria-label="Workflow name"
          className="font-semibold text-sm bg-transparent border-b border-transparent hover:border-muted-foreground focus:border-primary focus:outline-none w-48"
        />
        <div className="flex-1" />
        {/* Undo / Redo */}
        <button
          onClick={undo}
          disabled={!canUndo}
          aria-label="Undo"
          title="Undo (Ctrl+Z)"
          className="text-xs px-2 py-1 border rounded hover:bg-muted disabled:opacity-40"
        >↩ Undo</button>
        <button
          onClick={redo}
          disabled={!canRedo}
          aria-label="Redo"
          title="Redo (Ctrl+Y)"
          className="text-xs px-2 py-1 border rounded hover:bg-muted disabled:opacity-40"
        >↪ Redo</button>
        {/* Templates */}
        <button
          onClick={() => setShowTemplates(true)}
          aria-label="Open templates"
          className="text-xs px-2 py-1 border rounded hover:bg-muted"
        >⚡ Templates</button>
        {/* Validate */}
        <button
          onClick={() => {
            const errs = validate(nodes);
            setValidationErrors(errs);
            setShowValidation(true);
            if (errs.length === 0) toast({ kind: 'success', message: 'Workflow is valid' });
          }}
          aria-label="Validate workflow"
          className={`text-xs px-2 py-1 border rounded hover:bg-muted ${errors.length > 0 && showValidation ? 'border-red-400 text-red-600' : ''}`}
        >
          {errors.length > 0 && showValidation ? `⚠ ${errors.length} issue${errors.length > 1 ? 's' : ''}` : '✓ Validate'}
        </button>
        {savedWorkflows && savedWorkflows.length > 0 && (
          <select
            onChange={(e) => { if (e.target.value) loadWorkflow(e.target.value); }}
            className="text-xs border rounded px-2 py-1 bg-background"
            defaultValue=""
            aria-label="Load saved workflow"
          >
            <option value="">Load saved…</option>
            {savedWorkflows.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        )}
        <button
          onClick={() => { setNodes([]); setEdges([]); setCurrentWfId(null); setWorkflowName('My Workflow'); historyStack.current = [{ nodes: [], edges: [] }]; historyIdx.current = 0; setValidationErrors([]); }}
          className="text-xs px-2 py-1 border rounded hover:bg-muted"
        >New</button>
        <button onClick={save} aria-label="Save workflow" className="text-xs px-3 py-1 bg-primary text-primary-foreground rounded hover:opacity-90">Save</button>
        <button onClick={() => run(true)} disabled={running} aria-label="Dry Run" className="text-xs px-3 py-1 bg-yellow-500 text-foreground rounded hover:bg-yellow-600 disabled:opacity-50">Dry Run</button>
        <button onClick={() => run(false)} disabled={running} className="text-xs px-3 py-1 bg-green-600 text-foreground rounded hover:bg-green-700 disabled:opacity-50">{running ? 'Running…' : '▶ Run'}</button>
      </div>

      {/* Validation banner */}
      {showValidation && errors.length > 0 && (
        <div className="flex items-center gap-2 px-4 py-2 bg-red-50 dark:bg-red-950/30 border-b border-red-200 text-xs text-red-700">
          <span className="font-medium">Validation issues:</span>
          {errors.map((e, i) => <span key={i}>• {e}</span>)}
          <button onClick={() => setShowValidation(false)} className="ml-auto text-red-400 hover:text-red-600">✕</button>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        {/* Left: Node Palette */}
        <div className="w-48 border-r bg-card flex flex-col shrink-0">
          <div className="p-2 text-xs font-semibold text-muted-foreground border-b">Node Palette</div>
          <div className="p-2 border-b">
            <textarea
              value={nlGoal}
              onChange={(e) => setNlGoal(e.target.value)}
              rows={2}
              aria-label="Natural language workflow description"
              className="w-full text-xs border rounded p-1 resize-none bg-background"
              placeholder="Describe workflow…"
            />
            <button
              onClick={generateFromNL}
              disabled={generating || !nlGoal.trim()}
              aria-label="Generate workflow from natural language"
              className="w-full mt-1 text-xs bg-purple-600 text-foreground rounded py-1 disabled:opacity-50"
            >{generating ? '…' : '✨ Generate'}</button>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {PALETTE_NODES.map((n) => (
              <button
                key={n.type}
                draggable={true}
                onDragStart={(e) => onDragStart(e, n.type, n.label)}
                onClick={() => addNode(n.type, n.label)}
                aria-label={`Add ${n.label} node`}
                className={`w-full text-left text-xs p-2 rounded border ${NODE_COLORS[n.type] ?? ''} hover:opacity-90 transition-opacity cursor-grab active:cursor-grabbing`}
              >{NODE_ICONS[n.type]} {n.label}</button>
            ))}
          </div>
        </div>

        {/* Center: Canvas */}
        <div className="flex-1 relative" onDragOver={onDragOver} onDrop={onDrop}>
          {isEmpty && (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-muted-foreground pointer-events-none z-10">
              <div className="text-5xl mb-3">🔧</div>
              <div className="text-lg font-medium">Build your workflow</div>
              <div className="text-sm mt-1">Drag nodes from the palette or generate from natural language</div>
            </div>
          )}
          <ReactFlow
            nodes={nodes} edges={edges}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => setSelectedNode(node)}
            onPaneClick={() => setSelectedNode(null)}
            nodeTypes={NODE_TYPES}
            fitView snapToGrid snapGrid={SNAP_GRID}
            deleteKeyCode={null}   /* handled manually in onKeyDown */
            connectionMode={ConnectionMode.Strict}
            connectionLineStyle={CONNECTION_LINE_STYLE}
            isValidConnection={isValidConnection}
          >
            <Background variant={BackgroundVariant.Dots} gap={16} />
            <Controls />
            <MiniMap style={{ background: '#f8fafc' }} />
          </ReactFlow>
        </div>

        {/* Right: Inspector */}
        <div className="w-64 border-l bg-card flex flex-col shrink-0">
          <div className="p-2 text-xs font-semibold text-muted-foreground border-b">
            {selectedNode ? `Inspector — ${(selectedNode.data as WorkflowNodeData).type}` : 'Inspector'}
          </div>
          {selectedNode ? (
            <div className="p-3 space-y-3 text-xs overflow-y-auto flex-1">
              {/* Common: Label */}
              <div>
                <label htmlFor="node-label" className="text-muted-foreground block mb-1">Label</label>
                <input
                  id="node-label"
                  value={String((selectedNode.data as WorkflowNodeData).label ?? '')}
                  onChange={(e) => {
                    updateSelectedNodeData({ label: e.target.value });
                  }}
                  className="w-full border rounded px-2 py-1 bg-background"
                />
              </div>
              {/* Common: Description */}
              <div>
                <label htmlFor="node-description" className="text-muted-foreground block mb-1">Description</label>
                <textarea
                  id="node-description"
                  value={String((selectedNode.data as WorkflowNodeData).description ?? (selectedNode.data as WorkflowNodeData).subtitle ?? '')}
                  onChange={(e) => {
                    updateSelectedNodeData({ description: e.target.value, subtitle: e.target.value });
                  }}
                  rows={2}
                  className="w-full border rounded px-2 py-1 resize-none bg-background"
                />
              </div>

              {/* Type-Specific config section */}
              <div className="border-t pt-2">
                <p className="text-[10px] text-muted-foreground font-semibold uppercase tracking-wide mb-2">
                  {String((selectedNode.data as WorkflowNodeData).type)} Config
                </p>
                <TypeSpecificConfig
                  nodeData={selectedNode.data as WorkflowNodeData}
                  onChange={updateSelectedNodeData}
                />
              </div>

              <div className="text-[10px] text-muted-foreground border-t pt-2">
                Node ID: {selectedNode.id}<br />
                Type: {String((selectedNode.data as WorkflowNodeData).type)}
              </div>
              <button
                onClick={() => {
                  setNodes((nds) => {
                    const filtered = nds.filter((n) => n.id !== selectedNode.id);
                    setEdges((eds) => {
                      const filteredEdges = eds.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id);
                      pushHistory(filtered, filteredEdges);
                      return filteredEdges;
                    });
                    return filtered;
                  });
                  setSelectedNode(null);
                }}
                aria-label="Delete selected node"
                className="w-full text-xs bg-red-50 text-red-600 border border-red-200 rounded py-1 hover:bg-red-100"
              >Delete Node</button>
            </div>
          ) : (
            <div className="p-3 text-xs text-muted-foreground">Click a node to inspect and configure it</div>
          )}
          {runOutput && (
            <div className="border-t p-2 overflow-auto">
              <div className="text-xs font-semibold mb-1">Run Output</div>
              <pre className="text-[10px] bg-muted rounded p-2 overflow-auto max-h-40">{runOutput}</pre>
            </div>
          )}
        </div>
      </div>

      {/* Templates Modal */}
      {showTemplates && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={(e) => { if (e.target === e.currentTarget) setShowTemplates(false); }}
          role="dialog"
          aria-label="Workflow templates"
          aria-modal="true"
        >
          <div className="bg-card border border-border rounded-2xl w-full max-w-2xl max-h-[80vh] overflow-hidden shadow-xl">
            <div className="flex items-center justify-between px-5 py-4 border-b">
              <h2 className="font-semibold text-sm">⚡ Starter Templates</h2>
              <button onClick={() => setShowTemplates(false)} className="text-muted-foreground hover:text-foreground" aria-label="Close templates modal">✕</button>
            </div>
            <div className="grid grid-cols-2 gap-3 p-5 overflow-y-auto max-h-[60vh]">
              {WORKFLOW_TEMPLATES.map((tmpl) => (
                <button
                  key={tmpl.id}
                  onClick={() => loadTemplate(tmpl)}
                  className="text-left p-4 border border-border rounded-xl hover:border-primary hover:bg-muted/50 transition-colors"
                  aria-label={`Load template ${tmpl.name}`}
                >
                  <p className="font-medium text-sm">{tmpl.name}</p>
                  <p className="text-xs text-muted-foreground mt-1">{tmpl.description}</p>
                  <p className="text-[10px] text-muted-foreground mt-2">{tmpl.nodes.length} nodes · {tmpl.edges.length} edges</p>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Public export wrapped with ReactFlowProvider ─────────────────────────────

export function WorkflowBuilderPage() {
  return (
    <ReactFlowProvider>
      <WorkflowBuilderInner />
    </ReactFlowProvider>
  );
}

export default WorkflowBuilderPage;
