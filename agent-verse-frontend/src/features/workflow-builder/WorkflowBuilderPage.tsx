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
import { Download } from 'lucide-react';
import { useAuthStore } from '../../stores/auth';
import { toast } from '../../stores/toast';
import { workflowsApi, apiFetch } from '../../lib/api/client';
import { MissionControlLayout } from '@/components/ui/MissionControlLayout';

// ─── Node Types ──────────────────────────────────────────────────────────────

const NODE_COLORS: Record<string, string> = {
  trigger:        'bg-verified-green/15 border-verified-green/60 text-verified-green',
  tool_call:      'bg-telemetry-cyan/15 border-telemetry-cyan/60 text-telemetry-cyan',
  agent_step:     'bg-neural-violet/20 border-neural-violet/60 text-neural-violet',
  decision:       'bg-risk-amber/15 border-risk-amber/60 text-risk-amber',
  parallel:       'bg-orange-500/15 border-orange-400/60 text-orange-400',
  loop:           'bg-telemetry-cyan/10 border-telemetry-cyan/40 text-telemetry-cyan/80',
  human_approval: 'bg-mission-red/15 border-mission-red/60 text-mission-red',
  delay:          'bg-white/5 border-white/20 text-white/50',
  rag:            'bg-teal-500/15 border-teal-400/60 text-teal-400',
  skill:          'bg-neural-violet/25 border-neural-violet text-white/90',
  end:            'bg-white/5 border-white/15 text-white/40',
};

const NODE_ICONS: Record<string, string> = {
  trigger: '▶', tool_call: '🔧', agent_step: '🤖', decision: '❓',
  parallel: '⫸', loop: '↻', human_approval: '👤', delay: '⏱', end: '⬛',
  rag: '🔍', skill: '⚡',
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
  // run status from last execution
  runStatus?: 'success' | 'error' | 'skipped';
  [key: string]: unknown;
}

function WorkflowNode({ data, selected }: { data: WorkflowNodeData; selected?: boolean }) {
  const color = NODE_COLORS[data.type] ?? 'bg-white/5 border-white/20 text-white/60';
  const hasValidationError = data.type === 'tool_call' && !data.tool;
  return (
    <div
      className={`relative rounded-lg border-2 p-3 min-w-[140px] shadow-lg text-xs ${color} ${
        selected ? 'ring-2 ring-neural-violet ring-offset-1 ring-offset-command-black' : ''
      } ${
        data.status === 'running'  ? 'animate-pulse ring-2 ring-neural-violet/60' :
        data.status === 'complete' ? '!bg-verified-green/15 !border-verified-green' :
        data.status === 'failed'   ? '!bg-mission-red/15 !border-mission-red' : ''
      }`}
    >
      {/* Run-status indicator dot */}
      {data.runStatus && (
        <div className={`absolute -top-1.5 -right-1.5 w-3 h-3 rounded-full border-2 border-command-black ${
          data.runStatus === 'success' ? 'bg-verified-green' : 'bg-mission-red'
        }`} />
      )}
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-neural-violet/60 !border-neural-violet !w-4 !h-4"
      />
      <div className="flex items-center gap-1.5 font-semibold mb-0.5">
        <span>{NODE_ICONS[data.type] ?? '◻'}</span>
        <span className="truncate">{String(data.label)}</span>
        {hasValidationError && (
          <span className="ml-auto text-risk-amber text-[9px] font-bold" title="Tool not configured">⚠</span>
        )}
      </div>
      {data.subtitle && (
        <div className="text-[10px] opacity-60 truncate">{String(data.subtitle)}</div>
      )}
      {data.status && (
        <div
          className={`mt-1 text-[10px] font-medium ${
            data.status === 'running'  ? 'text-neural-violet'   :
            data.status === 'complete' ? 'text-verified-green'  :
            data.status === 'failed'   ? 'text-mission-red'     : 'opacity-50'
          }`}
        >
          ● {data.status}
        </div>
      )}
      {/* Multi-handle source handles for decision and parallel nodes */}
      {data.type === 'decision' ? (
        <>
          <Handle
            type="source"
            position={Position.Bottom}
            id="true"
            style={{ left: '30%', background: '#22C55E' }}
            className="!border-command-black !w-3 !h-3"
          />
          <Handle
            type="source"
            position={Position.Bottom}
            id="false"
            style={{ left: '70%', background: '#EF4444' }}
            className="!border-command-black !w-3 !h-3"
          />
          <div style={{ position: 'absolute', bottom: -18, left: '18%', fontSize: '10px', color: '#22C55E', pointerEvents: 'none' }}>
            True
          </div>
          <div style={{ position: 'absolute', bottom: -18, left: '62%', fontSize: '10px', color: '#EF4444', pointerEvents: 'none' }}>
            False
          </div>
        </>
      ) : data.type === 'parallel' ? (
        <>
          <Handle type="source" position={Position.Bottom} id="branch-1" style={{ left: '20%' }} className="!bg-neural-violet/60 !border-neural-violet !w-3 !h-3" />
          <Handle type="source" position={Position.Bottom} id="branch-2" style={{ left: '50%' }} className="!bg-neural-violet/60 !border-neural-violet !w-3 !h-3" />
          <Handle type="source" position={Position.Bottom} id="branch-3" style={{ left: '80%' }} className="!bg-neural-violet/60 !border-neural-violet !w-3 !h-3" />
        </>
      ) : (
        <Handle
          type="source"
          position={Position.Bottom}
          className="!bg-neural-violet/60 !border-neural-violet !w-4 !h-4"
        />
      )}
    </div>
  );
}

const NODE_TYPES = { workflow: WorkflowNode };
export const SNAP_GRID: [number, number] = [16, 16]; // module-level constant — prevents ReactFlow useEffect loop

const CONNECTION_LINE_STYLE: React.CSSProperties = {
  stroke: '#7C3AED',
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
  { type: 'rag',            label: 'RAG Retrieval'      },
  { type: 'skill',          label: 'Skill'              },
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
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 's3', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e4', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
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
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
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
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 's3', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e4', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
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
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 's3', label: 'Yes', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#ef4444', strokeWidth: 2 } },
      { id: 'e4', source: 's2', target: 's4', label: 'No', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e5', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e6', source: 's4', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
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
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e3', source: 's1', target: 's3', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e4', source: 's2', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e5', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
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
      { id: 'e1', source: 'trigger', target: 's1', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e2', source: 's1', target: 's2', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e3', source: 's2', target: 's3', label: 'Over', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#ef4444', strokeWidth: 2 } },
      { id: 'e4', source: 's3', target: 'end', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#7C3AED', strokeWidth: 2 } },
      { id: 'e5', source: 's2', target: 'end', label: 'OK', markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: '#22c55e', strokeWidth: 2 } },
    ],
  },
];

// ─── Tool Selector ────────────────────────────────────────────────────────────

function ToolSelector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data: tools = [] } = useQuery<Record<string, unknown>[]>({
    queryKey: ['rpa-tools'],
    queryFn: () => apiFetch<Record<string, unknown>[]>('/rpa/tools').catch(() => []),
    staleTime: 5 * 60 * 1000,
  });

  return (
    <div className="relative">
      <input
        list="wf-tool-options"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search tools… (e.g. jira_create_issue)"
        className="w-full border border-neural-violet/20 rounded px-2 py-1 bg-command-black text-white/80 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-neural-violet/40 placeholder-white/25"
        aria-label="Tool selector"
      />
      <datalist id="wf-tool-options">
        {tools.map((t) => (
          <option key={t.name as string} value={t.name as string}>
            {t.description as string | undefined}
          </option>
        ))}
      </datalist>
    </div>
  );
}

// ─── Type-Specific Inspector ──────────────────────────────────────────────────

function TypeSpecificConfig({
  nodeData,
  onChange,
}: {
  nodeData: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const type = nodeData.type;
  const inputCls = 'w-full border border-neural-violet/20 rounded px-2 py-1 bg-command-black text-white/80 text-xs focus:outline-none focus:ring-1 focus:ring-neural-violet/40';
  const labelCls = 'text-white/40 block mb-1';

  const field = (
    id: string,
    label: string,
    value: string | number | undefined,
    handler: (v: string) => void,
    placeholder?: string,
    inputType: 'text' | 'number' | 'textarea' = 'text',
  ) => (
    <div key={id}>
      <label htmlFor={id} className={labelCls}>{label}</label>
      {inputType === 'textarea' ? (
        <textarea
          id={id}
          value={String(value ?? '')}
          onChange={(e) => handler(e.target.value)}
          placeholder={placeholder}
          rows={2}
          className={`${inputCls} resize-none`}
        />
      ) : (
        <input
          id={id}
          type={inputType}
          value={value != null ? String(value) : ''}
          onChange={(e) => handler(e.target.value)}
          placeholder={placeholder}
          className={inputCls}
        />
      )}
    </div>
  );

  if (type === 'trigger') return (
    <div className="space-y-2">
      <div>
        <label htmlFor="trigger-type" className={labelCls}>Trigger Type</label>
        <select
          id="trigger-type"
          value={nodeData.trigger_type ?? 'manual'}
          onChange={(e) => onChange({ trigger_type: e.target.value })}
          className={inputCls}
        >
          <option value="manual" className="bg-command-black">Manual</option>
          <option value="cron" className="bg-command-black">CRON Schedule</option>
          <option value="webhook" className="bg-command-black">Webhook</option>
          <option value="event" className="bg-command-black">Event</option>
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
        <label htmlFor="tool-selector" className={labelCls}>Tool</label>
        <ToolSelector
          value={nodeData.tool ?? ''}
          onChange={(v) => onChange({ tool: v })}
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
        <label htmlFor="max-conc" className={labelCls}>Max Concurrency</label>
        <input
          id="max-conc"
          type="number"
          min={1} max={20}
          value={nodeData.max_concurrency ?? 5}
          onChange={(e) => onChange({ max_concurrency: parseInt(e.target.value, 10) })}
          className={inputCls}
        />
      </div>
    </div>
  );

  if (type === 'loop') return (
    <div className="space-y-2">
      {field('iterator', 'Iterator Expression', nodeData.iterator, (v) => onChange({ iterator: v }), 'item in items')}
      <div>
        <label htmlFor="max-iter" className={labelCls}>Max Iterations</label>
        <input
          id="max-iter"
          type="number"
          min={1} max={1000}
          value={nodeData.max_iterations ?? 100}
          onChange={(e) => onChange({ max_iterations: parseInt(e.target.value, 10) })}
          className={inputCls}
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
        <label htmlFor="timeout-min" className={labelCls}>Timeout (minutes)</label>
        <input
          id="timeout-min"
          type="number"
          min={1}
          value={nodeData.timeout_minutes ?? 60}
          onChange={(e) => onChange({ timeout_minutes: parseInt(e.target.value, 10) })}
          className={inputCls}
        />
      </div>
    </div>
  );

  if (type === 'delay') return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="delay-dur" className={labelCls}>Duration</label>
          <input
            id="delay-dur"
            type="number"
            min={1}
            value={nodeData.duration ?? 1}
            onChange={(e) => onChange({ duration: parseInt(e.target.value, 10) })}
            className={inputCls}
          />
        </div>
        <div>
          <label htmlFor="delay-unit" className={labelCls}>Unit</label>
          <select
            id="delay-unit"
            value={nodeData.duration_unit ?? 'minutes'}
            onChange={(e) => onChange({ duration_unit: e.target.value })}
            className={inputCls}
          >
            <option value="seconds" className="bg-command-black">Seconds</option>
            <option value="minutes" className="bg-command-black">Minutes</option>
            <option value="hours" className="bg-command-black">Hours</option>
            <option value="days" className="bg-command-black">Days</option>
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

  if (type === 'rag') return (
    <div className="space-y-2">
      {field('collection-id', 'Collection ID', nodeData.collection_id as string | undefined, (v) => onChange({ collection_id: v }), 'col-uuid')}
      {field('query-tmpl', 'Query Template', nodeData.query_template as string | undefined, (v) => onChange({ query_template: v }), '{{goal}}', 'textarea')}
      <div>
        <label htmlFor="rag-strategy" className={labelCls}>Strategy</label>
        <select
          id="rag-strategy"
          value={(nodeData.strategy as string | undefined) ?? 'hybrid'}
          onChange={(e) => onChange({ strategy: e.target.value })}
          className={inputCls}
        >
          <option value="hybrid" className="bg-command-black">Hybrid</option>
          <option value="vector" className="bg-command-black">Vector</option>
          <option value="lexical" className="bg-command-black">Lexical</option>
        </select>
      </div>
      <div>
        <label htmlFor="rag-topk" className={labelCls}>Top K</label>
        <input
          id="rag-topk"
          type="number"
          min={1} max={20}
          value={(nodeData.top_k as number | undefined) ?? 5}
          onChange={(e) => onChange({ top_k: parseInt(e.target.value, 10) })}
          className={inputCls}
        />
      </div>
    </div>
  );

  if (type === 'skill') return (
    <div className="space-y-2">
      {field('skill-id', 'Skill ID (optional)', nodeData.skill_id as string | undefined, (v) => onChange({ skill_id: v }), 'skill-code-review')}
      {field('skill-goal', 'Goal (for auto-select)', nodeData.goal_template as string | undefined, (v) => onChange({ goal_template: v }), 'review PR for security', 'textarea')}
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
  const [nodeRunData, setNodeRunData] = useState<Record<string, {
    status: 'success' | 'error' | 'skipped';
    input?: unknown;
    output?: unknown;
    duration_ms?: number;
    error?: string;
  }>>({});
  const [showTemplates, setShowTemplates] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [showValidation, setShowValidation] = useState(false);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'unsaved' | 'saved' | 'error'>('idle');
  const nodeCounter = useRef(1);
  // Clipboard: stores a shallow copy of the last-copied node for Ctrl+C / Ctrl+V
  const clipboardNode = useRef<Node | null>(null);
  // Undo/redo history stack
  const historyStack = useRef<{ nodes: Node[]; edges: Edge[] }[]>([{ nodes: [], edges: [] }]);
  const historyIdx = useRef(0);
  // Debounce timer for auto-save
  const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
        style: { stroke: '#7C3AED', strokeWidth: 2 },
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

  // ── Auto-save existing workflows after nodes/edges settle (2 s debounce) ──

  useEffect(() => {
    if (!currentWfId) return;
    if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
    setSaveStatus('unsaved');
    autoSaveTimer.current = setTimeout(async () => {
      try {
        const definition = {
          steps: nodes.map((n) => {
            const nodeData = n.data as WorkflowNodeData;
            return { id: n.id, position: n.position, ...nodeData };
          }),
          edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target, label: e.label != null ? String(e.label) : undefined })),
        };
        await workflowsApi.update(currentWfId, { name: workflowName, definition });
        setSaveStatus('saved');
      } catch {
        setSaveStatus('error');
      }
    }, 2000);
    return () => { if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current); };
  }, [nodes, edges, currentWfId, workflowName]);

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
          style: { stroke: '#7C3AED', strokeWidth: 2 },
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
    setNodeRunData({});
    // Animate: set all nodes to running
    if (!dryRun) {
      setNodes((nds) => nds.map((n) => ({ ...n, data: { ...n.data, status: 'running', runStatus: undefined } })));
    }
    try {
      const data = await workflowsApi.run(currentWfId!, dryRun);
      setRunOutput(JSON.stringify(data, null, 2));

      // Populate per-node run data from response
      const nodeMap: Record<string, {
        status: 'success' | 'error' | 'skipped';
        input?: unknown;
        output?: unknown;
        duration_ms?: number;
        error?: string;
      }> = {};
      const stepResults = (
        (data as Record<string, unknown>).node_results ??
        (data as Record<string, unknown>).steps ??
        []
      ) as Record<string, unknown>[];

      stepResults.forEach((step) => {
        const nid = (step.node_id ?? step.id) as string | undefined;
        if (nid) {
          nodeMap[nid] = {
            status: step.status === 'failed' ? 'error' : 'success',
            input: step.input,
            output: step.output,
            duration_ms: step.duration_ms as number | undefined,
            error: step.error as string | undefined,
          };
        }
      });
      setNodeRunData(nodeMap);

      // Animate: set all nodes to complete/dry_run status with runStatus dot
      setNodes((nds) => nds.map((n) => ({
        ...n,
        data: {
          ...n.data,
          status: data.status === 'complete' ? 'complete' : dryRun ? 'complete' : n.data.status,
          runStatus: nodeMap[n.id]?.status,
        },
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
        style: { stroke: '#7C3AED', strokeWidth: 2 },
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

  const toolbarBtnCls = 'text-xs px-2 py-1 border border-neural-violet/20 rounded text-white/60 hover:bg-neural-violet/10 hover:text-white/80 disabled:opacity-40 transition-colors';

  return (
    <div className="flex flex-col h-full bg-command-black overflow-hidden">
      {/* Cockpit Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-neural-violet/20 bg-panel-graphite/80 flex-wrap shrink-0">
        {/* Workflow name input */}
        <input
          value={workflowName}
          onChange={(e) => setWorkflowName(e.target.value)}
          aria-label="Workflow name"
          className="font-semibold text-sm bg-transparent border-b border-transparent hover:border-neural-violet/30 focus:border-neural-violet focus:outline-none w-48 text-white placeholder-white/30"
        />
        <div className="flex-1" />
        {/* Undo / Redo */}
        <button onClick={undo} disabled={!canUndo} aria-label="Undo" title="Undo (Ctrl+Z)" className={toolbarBtnCls}>↩ Undo</button>
        <button onClick={redo} disabled={!canRedo} aria-label="Redo" title="Redo (Ctrl+Y)" className={toolbarBtnCls}>↪ Redo</button>
        {/* Templates */}
        <button onClick={() => setShowTemplates(true)} aria-label="Open templates" className={toolbarBtnCls}>⚡ Templates</button>
        {/* Validate */}
        <button
          onClick={() => {
            const errs = validate(nodes);
            setValidationErrors(errs);
            setShowValidation(true);
            if (errs.length === 0) toast({ kind: 'success', message: 'Workflow is valid' });
          }}
          aria-label="Validate workflow"
          className={`${toolbarBtnCls} ${errors.length > 0 && showValidation ? '!border-mission-red/50 !text-mission-red' : ''}`}
        >
          {errors.length > 0 && showValidation ? `⚠ ${errors.length} issue${errors.length > 1 ? 's' : ''}` : '✓ Validate'}
        </button>
        {savedWorkflows && savedWorkflows.length > 0 && (
          <select
            onChange={(e) => { if (e.target.value) loadWorkflow(e.target.value); }}
            className="text-xs border border-neural-violet/20 rounded px-2 py-1 bg-command-black text-white/60 focus:outline-none focus:ring-1 focus:ring-neural-violet/40"
            defaultValue=""
            aria-label="Load saved workflow"
          >
            <option value="" className="bg-command-black">Load saved…</option>
            {savedWorkflows.map((w) => <option key={w.id} value={w.id} className="bg-command-black">{w.name}</option>)}
          </select>
        )}
        <button
          onClick={() => { setNodes([]); setEdges([]); setCurrentWfId(null); setWorkflowName('My Workflow'); historyStack.current = [{ nodes: [], edges: [] }]; historyIdx.current = 0; setValidationErrors([]); }}
          className={toolbarBtnCls}
        >New</button>
        <button onClick={save} aria-label="Save workflow" className="text-xs px-3 py-1 bg-neural-violet text-white rounded hover:bg-neural-violet/90 transition-colors shadow-sm shadow-neural-violet/20">Save</button>
        {/* Auto-save status indicator */}
        {currentWfId && saveStatus !== 'idle' && (
          <span className={`text-xs font-mono ${saveStatus === 'saved' ? 'text-verified-green' : saveStatus === 'error' ? 'text-mission-red' : 'text-risk-amber'}`}>
            {saveStatus === 'saved' ? '● Saved' : saveStatus === 'error' ? '● Save failed' : '● Unsaved'}
          </span>
        )}
        <button onClick={() => run(true)} disabled={running} aria-label="Dry Run" className="text-xs px-3 py-1 bg-risk-amber/80 text-white rounded hover:bg-risk-amber disabled:opacity-50 transition-colors">Dry Run</button>
        <button onClick={() => run(false)} disabled={running} className="text-xs px-3 py-1 bg-verified-green/80 text-white rounded hover:bg-verified-green disabled:opacity-50 transition-colors">{running ? 'Running…' : '▶ Run'}</button>
      </div>

      {/* Validation banner */}
      {showValidation && errors.length > 0 && (
        <div className="flex items-center gap-2 px-4 py-2 bg-mission-red/10 border-b border-mission-red/20 text-xs text-mission-red shrink-0">
          <span className="font-medium">Validation issues:</span>
          {errors.map((e, i) => <span key={i}>• {e}</span>)}
          <button onClick={() => setShowValidation(false)} className="ml-auto text-mission-red/60 hover:text-mission-red">✕</button>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Left: Node Palette */}
        <div className="w-48 border-r border-neural-violet/20 bg-panel-graphite flex flex-col shrink-0">
          <div className="p-2 text-xs font-semibold text-white/30 border-b border-neural-violet/15 uppercase tracking-wider">Node Palette</div>
          <div className="p-2 border-b border-neural-violet/15">
            <textarea
              value={nlGoal}
              onChange={(e) => setNlGoal(e.target.value)}
              rows={2}
              aria-label="Natural language workflow description"
              className="w-full text-xs border border-neural-violet/20 rounded p-1 resize-none bg-command-black text-white/80 placeholder-white/25 focus:outline-none focus:ring-1 focus:ring-neural-violet/40"
              placeholder="Describe workflow…"
            />
            <button
              onClick={generateFromNL}
              disabled={generating || !nlGoal.trim()}
              aria-label="Generate workflow from natural language"
              className="w-full mt-1 text-xs bg-neural-violet text-white rounded py-1 disabled:opacity-50 hover:bg-neural-violet/90 transition-colors shadow-sm shadow-neural-violet/20"
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
                className={`w-full text-left text-xs p-2 rounded border-2 ${NODE_COLORS[n.type] ?? ''} hover:opacity-90 transition-opacity cursor-grab active:cursor-grabbing`}
              >{NODE_ICONS[n.type]} {n.label}</button>
            ))}
          </div>
        </div>

        {/* Center: Canvas */}
        <div className="flex-1 relative bg-command-black" onDragOver={onDragOver} onDrop={onDrop}>
          {isEmpty && (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-white/20 pointer-events-none z-10">
              <div className="text-5xl mb-3 opacity-40">⬡</div>
              <div className="text-lg font-medium">Build your workflow</div>
              <div className="text-sm mt-1 text-white/30">Drag nodes from the palette or generate from natural language</div>
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
            style={{ background: '#080A12' }}
          >
            <Background variant={BackgroundVariant.Dots} gap={16} color="#7C3AED22" />
            <Controls className="!bg-panel-graphite !border-neural-violet/20" />
            <MiniMap style={{ background: '#080A12', border: '1px solid rgba(124,58,237,0.2)' }} maskColor="rgba(8,10,18,0.8)" />
          </ReactFlow>
        </div>

        {/* Right: Inspector */}
        <div className="w-64 border-l border-neural-violet/20 bg-panel-graphite flex flex-col shrink-0">
          <div className="p-2 text-xs font-semibold text-white/30 border-b border-neural-violet/15 uppercase tracking-wider">
            {selectedNode ? `Inspector — ${(selectedNode.data as WorkflowNodeData).type}` : 'Inspector'}
          </div>
          {selectedNode ? (
            <div className="p-3 space-y-3 text-xs overflow-y-auto flex-1">
              {/* Common: Label */}
              <div>
                <label htmlFor="node-label" className="text-white/40 block mb-1">Label</label>
                <input
                  id="node-label"
                  value={String((selectedNode.data as WorkflowNodeData).label ?? '')}
                  onChange={(e) => {
                    updateSelectedNodeData({ label: e.target.value });
                  }}
                  className="w-full border border-neural-violet/20 rounded px-2 py-1 bg-command-black text-white/80 focus:outline-none focus:ring-1 focus:ring-neural-violet/40"
                />
              </div>
              {/* Common: Description */}
              <div>
                <label htmlFor="node-description" className="text-white/40 block mb-1">Description</label>
                <textarea
                  id="node-description"
                  value={String((selectedNode.data as WorkflowNodeData).description ?? (selectedNode.data as WorkflowNodeData).subtitle ?? '')}
                  onChange={(e) => {
                    updateSelectedNodeData({ description: e.target.value, subtitle: e.target.value });
                  }}
                  rows={2}
                  className="w-full border border-neural-violet/20 rounded px-2 py-1 resize-none bg-command-black text-white/80 focus:outline-none focus:ring-1 focus:ring-neural-violet/40"
                />
              </div>

              {/* Type-Specific config section */}
              <div className="border-t border-neural-violet/15 pt-2">
                <p className="text-[10px] text-white/30 font-semibold uppercase tracking-wide mb-2">
                  {String((selectedNode.data as WorkflowNodeData).type)} Config
                </p>
                <TypeSpecificConfig
                  nodeData={selectedNode.data as WorkflowNodeData}
                  onChange={updateSelectedNodeData}
                />
              </div>

              <div className="text-[10px] text-white/25 border-t border-neural-violet/15 pt-2 font-mono">
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
                className="w-full text-xs bg-mission-red/10 text-mission-red border border-mission-red/30 rounded py-1 hover:bg-mission-red/20 transition-colors"
              >Delete Node</button>

              {/* Per-node run output — shown after a run */}
              {nodeRunData[selectedNode.id] && (
                <div className="border-t border-neural-violet/15 pt-3 space-y-2">
                  <div className="flex items-center gap-2">
                    <p className="text-[10px] font-semibold text-white/30 uppercase tracking-wide">
                      Last Run Output
                    </p>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium border ${
                      nodeRunData[selectedNode.id].status === 'success'
                        ? 'bg-verified-green/15 text-verified-green border-verified-green/30'
                        : 'bg-mission-red/15 text-mission-red border-mission-red/30'
                    }`}>
                      {nodeRunData[selectedNode.id].status}
                      {nodeRunData[selectedNode.id].duration_ms != null &&
                        ` · ${nodeRunData[selectedNode.id].duration_ms}ms`}
                    </span>
                  </div>

                  {nodeRunData[selectedNode.id].input != null && (
                    <div>
                      <p className="text-[10px] font-medium text-white/30 mb-1">INPUT</p>
                      <pre className="text-[10px] bg-command-black/80 text-telemetry-cyan rounded p-2 overflow-auto max-h-24 font-mono whitespace-pre-wrap border border-neural-violet/15">
                        {JSON.stringify(nodeRunData[selectedNode.id].input, null, 2)}
                      </pre>
                    </div>
                  )}

                  {nodeRunData[selectedNode.id].output != null && (
                    <div>
                      <p className="text-[10px] font-medium text-white/30 mb-1">OUTPUT</p>
                      <pre className="text-[10px] bg-command-black/80 text-telemetry-cyan rounded p-2 overflow-auto max-h-24 font-mono whitespace-pre-wrap border border-neural-violet/15">
                        {typeof nodeRunData[selectedNode.id].output === 'string'
                          ? nodeRunData[selectedNode.id].output as string
                          : JSON.stringify(nodeRunData[selectedNode.id].output, null, 2)}
                      </pre>
                    </div>
                  )}

                  {nodeRunData[selectedNode.id].error && (
                    <div className="p-2 bg-mission-red/10 rounded border border-mission-red/20">
                      <p className="text-[10px] text-mission-red font-mono">
                        {nodeRunData[selectedNode.id].error}
                      </p>
                    </div>
                  )}

                  <button
                    onClick={() => {
                      const runEntry = nodeRunData[selectedNode.id];
                      const blob = new Blob([JSON.stringify(runEntry, null, 2)], { type: 'application/json' });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `node-${selectedNode.id}-output.json`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                    className="flex items-center gap-1 text-[10px] text-neural-violet hover:text-neural-violet/70 transition-colors"
                  >
                    <Download className="h-3 w-3" />
                    Export node data
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="p-3 text-xs text-white/30">Click a node to inspect and configure it</div>
          )}
          {runOutput && (
            <div className="border-t border-neural-violet/15 p-2 overflow-auto shrink-0">
              <div className="text-xs font-semibold mb-1 text-white/50">Run Output</div>
              <pre className="text-[10px] bg-command-black rounded p-2 overflow-auto max-h-40 text-telemetry-cyan font-mono border border-neural-violet/15">{runOutput}</pre>
            </div>
          )}
        </div>
      </div>

      {/* Templates Modal */}
      {showTemplates && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          onClick={(e) => { if (e.target === e.currentTarget) setShowTemplates(false); }}
          role="dialog"
          aria-label="Workflow templates"
          aria-modal="true"
        >
          <div className="bg-panel-graphite border border-neural-violet/30 rounded-2xl w-full max-w-2xl max-h-[80vh] overflow-hidden shadow-2xl shadow-neural-violet/10">
            <div className="flex items-center justify-between px-5 py-4 border-b border-neural-violet/20">
              <h2 className="font-semibold text-sm text-white flex items-center gap-2">
                <span className="text-neural-violet">⚡</span> Starter Templates
              </h2>
              <button onClick={() => setShowTemplates(false)} className="text-white/40 hover:text-white/80 transition-colors" aria-label="Close templates modal">✕</button>
            </div>
            <div className="grid grid-cols-2 gap-3 p-5 overflow-y-auto max-h-[60vh]">
              {WORKFLOW_TEMPLATES.map((tmpl) => (
                <button
                  key={tmpl.id}
                  onClick={() => loadTemplate(tmpl)}
                  className="text-left p-4 border border-neural-violet/20 rounded-xl hover:border-neural-violet/50 hover:bg-neural-violet/5 transition-[color,background-color,border-color,opacity,box-shadow,transform] group"
                  aria-label={`Load template ${tmpl.name}`}
                >
                  <p className="font-medium text-sm text-white group-hover:text-neural-violet transition-colors">{tmpl.name}</p>
                  <p className="text-xs text-white/40 mt-1">{tmpl.description}</p>
                  <p className="text-[10px] text-white/25 mt-2 font-mono">{tmpl.nodes.length} nodes · {tmpl.edges.length} edges</p>
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
    <MissionControlLayout showOperationalBar={false}>
      <ReactFlowProvider>
        <WorkflowBuilderInner />
      </ReactFlowProvider>
    </MissionControlLayout>
  );
}

export default WorkflowBuilderPage;
