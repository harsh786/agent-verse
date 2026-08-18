/**
 * All 14 workflow node type components.
 *
 * Each node wraps BaseWorkflowNode with type-specific visual identity
 * and exports a nodeTypes map for React Flow registration.
 *
 * Animations: nodeBounce on mount, selection ring pulse.
 */
import type { NodeProps } from '@xyflow/react';
import { Handle, Position } from '@xyflow/react';
import { motion } from 'framer-motion';
import { BaseWorkflowNode, type WorkflowNodeData } from './BaseWorkflowNode';
import { NODE_COLORS, type NodeType } from '../../design/tokens';
import { nodeBounce } from '../../design/motion';

// ── Shared base wrapper ───────────────────────────────────────────────────────

function makeNode(stepType: NodeType | string) {
  const NodeComponent = (props: NodeProps) => (
    <motion.div variants={nodeBounce} initial="initial" animate="animate" exit="exit">
      <BaseWorkflowNode
        {...props}
        data={{ ...(props.data as WorkflowNodeData), stepType }}
      />
    </motion.div>
  );
  NodeComponent.displayName = `${stepType}Node`;
  return NodeComponent;
}

// ── 14 node type components ────────────────────────────────────────────────────

export const TriggerNode = (props: NodeProps) => {
  const data = props.data as WorkflowNodeData;
  const colors = NODE_COLORS.trigger;
  return (
    <div>
      {/* Trigger has no input handle */}
      <div
        className={`
          relative min-w-[160px] rounded-full border px-4 py-2.5 flex items-center gap-2
          ${colors.bg} ${colors.border} ${colors.text}
          ${props.selected ? 'ring-2 ring-emerald-500 ring-offset-1' : ''}
          shadow-lg shadow-black/30
        `}
        role="button"
        tabIndex={0}
        aria-label={`Trigger: ${data.label ?? 'Trigger'}`}
      >
        <span className="text-base" aria-hidden>▶</span>
        <span className="text-sm font-semibold">
          {String((data as Record<string, unknown>).label ?? 'Trigger')}
        </span>
        {!!(data as Record<string, unknown>).triggerType && (
          <span className="ml-auto text-xs opacity-60">{String((data as Record<string, unknown>).triggerType)}</span>
        )}
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!w-3 !h-3 !bg-[#0F1826]/20 !border-2 !border-white/40 hover:!bg-emerald-400"
        aria-label="Trigger output"
      />
    </div>
  );
};

export const ToolNode = makeNode('tool');
export const LLMNode = makeNode('llm');
export const RAGNode = makeNode('rag');
export const HTTPNode = makeNode('http');

export const ConditionalNode = (props: NodeProps) => {
  const data = props.data as WorkflowNodeData;
  const colors = NODE_COLORS.conditional;
  return (
    <div>
      <Handle type="target" position={Position.Left}
        className="!w-3 !h-3 !bg-[#0F1826]/20 !border-2 !border-white/40 hover:!bg-amber-400"
        aria-label="Condition input" />
      {/* Diamond shape via CSS clip */}
      <div
        className={`
          relative w-[120px] h-[120px] flex items-center justify-center
          ${colors.bg} ${colors.border} border
          ${props.selected ? 'ring-2 ring-amber-500 ring-offset-1' : ''}
          shadow-lg shadow-black/30 rounded-sm
        `}
        style={{ clipPath: 'polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)' }}
        role="button"
        tabIndex={0}
        aria-label={`Condition: ${data.label ?? 'Condition'}`}
      >
        <div className="text-center -rotate-0">
          <div className={`text-xl mb-1 ${colors.text}`} aria-hidden>◇</div>
          <div className="text-xs text-white font-medium truncate max-w-[80px]">
            {(data.label as string) ?? 'Condition'}
          </div>
        </div>
      </div>
      {/* True branch (bottom) */}
      <Handle type="source" position={Position.Bottom} id="true"
        style={{ left: '75%' }}
        className="!w-3 !h-3 !bg-emerald-500/60 !border-2 !border-emerald-400 hover:!bg-emerald-400"
        aria-label="True branch output" />
      {/* False branch (bottom) */}
      <Handle type="source" position={Position.Bottom} id="false"
        style={{ left: '25%' }}
        className="!w-3 !h-3 !bg-red-500/60 !border-2 !border-red-400 hover:!bg-red-400"
        aria-label="False branch output" />
    </div>
  );
};

export const ParallelNode = (props: NodeProps) => {
  const data = props.data as WorkflowNodeData;
  const colors = NODE_COLORS.parallel;
  return (
    <div>
      <Handle type="target" position={Position.Left}
        className="!w-3 !h-3 !bg-[#0F1826]/20 !border-2 !border-white/40 hover:!bg-orange-400" />
      <div
        className={`
          relative min-w-[180px] rounded-xl border px-3 py-2
          ${colors.bg} ${colors.border}
          ${props.selected ? 'ring-2 ring-orange-500 ring-offset-1' : ''}
          shadow-lg shadow-black/30
        `}
        role="button"
        tabIndex={0}
        aria-label={`Parallel: ${data.label ?? 'Parallel'}`}
      >
        <div className={`flex items-center gap-2 ${colors.text} text-sm font-semibold`}>
          <span aria-hidden>⫸</span>
          <span>{(data.label as string) ?? 'Parallel'}</span>
        </div>
        <div className="flex gap-1 mt-2">
          {Array.from({ length: Number((data as Record<string, unknown>).branchCount ?? 2) }).map((_, i) => (
            <div key={i}
              className="flex-1 h-1.5 rounded-full bg-orange-500/30 border border-orange-400/30" />
          ))}
        </div>
      </div>
      <Handle type="source" position={Position.Right}
        className="!w-3 !h-3 !bg-[#0F1826]/20 !border-2 !border-white/40 hover:!bg-orange-400" />
    </div>
  );
};

export const HITLNode = makeNode('hitl');
export const ForeachNode = makeNode('foreach');
export const TransformNode = makeNode('transform');
export const SubWorkflowNode = makeNode('sub_workflow');

export const WaitNode = (props: NodeProps) => {
  const data = props.data as WorkflowNodeData;
  const colors = NODE_COLORS.wait;
  return (
    <div>
      <Handle type="target" position={Position.Left}
        className="!w-3 !h-3 !bg-[#0F1826]/20 !border-2 !border-white/40" />
      <div
        className={`
          relative min-w-[160px] rounded-xl border px-3 py-2.5
          ${colors.bg} ${colors.border}
          ${props.selected ? 'ring-2 ring-zinc-400 ring-offset-1' : ''}
          shadow-md shadow-black/20
        `}
        role="button"
        tabIndex={0}
        aria-label={`Wait: ${data.label ?? 'Wait'}`}
      >
        <div className={`flex items-center gap-2 ${colors.text} text-sm font-medium`}>
          <span className="text-base" aria-hidden>⏱</span>
          <span>{(data.duration as string) ?? (data.label as string) ?? 'Wait'}</span>
        </div>
      </div>
      <Handle type="source" position={Position.Right}
        className="!w-3 !h-3 !bg-[#0F1826]/20 !border-2 !border-white/40" />
    </div>
  );
};

export const CodeNode = makeNode('code');
export const SetVariableNode = makeNode('set_variable');
export const EmitEventNode = makeNode('emit_event');

// ── Node types registry (pass to ReactFlow) ───────────────────────────────────

export const workflowNodeTypes = {
  trigger:      TriggerNode,
  tool:         ToolNode,
  llm:          LLMNode,
  rag:          RAGNode,
  http:         HTTPNode,
  conditional:  ConditionalNode,
  parallel:     ParallelNode,
  hitl:         HITLNode,
  foreach:      ForeachNode,
  transform:    TransformNode,
  sub_workflow: SubWorkflowNode,
  wait:         WaitNode,
  code:         CodeNode,
  set_variable: SetVariableNode,
  emit_event:   EmitEventNode,
} as const;
