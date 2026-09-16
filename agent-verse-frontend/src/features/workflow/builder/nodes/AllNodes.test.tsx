import { render, screen } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';

// @xyflow/react's Handle needs the ReactFlow provider/DOM which jsdom lacks;
// stub the runtime pieces these node components import.
vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
}));

// framer-motion (used indirectly through BaseWorkflowNode) drives animation
// timers; stub motion.* so nodes render as plain DOM.
vi.mock('framer-motion', () => ({
  motion: new Proxy(
    {},
    {
      get: () => ({ children, ...props }: { children?: React.ReactNode; [k: string]: unknown }) => {
        const {
          animate: _a, variants: _v, initial: _i, whileHover: _wh, whileTap: _wt, transition: _t,
          ...rest
        } = props;
        return React.createElement('div', rest as Record<string, unknown>, children);
      },
    },
  ),
}));

import {
  TriggerNode, ToolNode, LLMNode, RAGNode, HTTPNode, ConditionalNode, ParallelNode,
  HITLNode, ForeachNode, TransformNode, SubWorkflowNode, WaitNode, CodeNode,
  SetVariableNode, EmitEventNode, OcrNode, RpaNode, OrgDecisionNode,
  DepartmentHandoffNode, CrossTeamReviewNode, ParallelDepartmentsNode,
  workflowNodeTypes,
} from './AllNodes';

// The node components only read data/selected/id from NodeProps.
function renderNode(Node: React.ComponentType<never>, data: Record<string, unknown>, selected = false) {
  return render(<Node {...({ id: 'n', data, selected } as never)} />);
}

afterEach(() => vi.restoreAllMocks());

describe('AllNodes — TriggerNode', () => {
  test('renders the custom label and trigger type', () => {
    renderNode(TriggerNode as never, { label: 'Webhook In', triggerType: 'webhook' });
    expect(screen.getByText('Webhook In')).toBeInTheDocument();
    expect(screen.getByText('webhook')).toBeInTheDocument();
    expect(screen.getByLabelText('Trigger: Webhook In')).toBeInTheDocument();
  });

  test('falls back to "Trigger" when no label is provided and omits type badge', () => {
    renderNode(TriggerNode as never, {});
    expect(screen.getAllByText('Trigger').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByLabelText('Trigger: Trigger')).toBeInTheDocument();
  });

  test('applies the selection ring when selected', () => {
    const { container } = renderNode(TriggerNode as never, { label: 'Start' }, true);
    expect(container.querySelector('.ring-2')).toBeTruthy();
  });
});

describe('AllNodes — ConditionalNode', () => {
  test('renders the label and condition aria-label', () => {
    renderNode(ConditionalNode as never, { label: 'Is Critical?' });
    expect(screen.getByText('Is Critical?')).toBeInTheDocument();
    expect(screen.getByLabelText('Condition: Is Critical?')).toBeInTheDocument();
  });

  test('falls back to "Condition" when label is absent', () => {
    renderNode(ConditionalNode as never, {});
    expect(screen.getByLabelText('Condition: Condition')).toBeInTheDocument();
  });
});

describe('AllNodes — ParallelNode', () => {
  test('renders label and one bar per branchCount', () => {
    const { container } = renderNode(ParallelNode as never, { label: 'Fan Out', branchCount: 4 });
    expect(screen.getByText('Fan Out')).toBeInTheDocument();
    expect(screen.getByLabelText('Parallel: Fan Out')).toBeInTheDocument();
    // 4 branch bars are rendered.
    expect(container.querySelectorAll('.bg-orange-500\\/30').length).toBe(4);
  });

  test('defaults to 2 branch bars and the "Parallel" label', () => {
    const { container } = renderNode(ParallelNode as never, {});
    expect(screen.getByLabelText('Parallel: Parallel')).toBeInTheDocument();
    expect(container.querySelectorAll('.bg-orange-500\\/30').length).toBe(2);
  });
});

describe('AllNodes — WaitNode', () => {
  test('prefers the duration value over the label', () => {
    renderNode(WaitNode as never, { label: 'Pause', duration: '5m' });
    expect(screen.getByText('5m')).toBeInTheDocument();
    expect(screen.getByLabelText('Wait: Pause')).toBeInTheDocument();
  });

  test('falls back to label then to "Wait"', () => {
    renderNode(WaitNode as never, {});
    expect(screen.getAllByText('Wait').length).toBeGreaterThanOrEqual(1);
  });
});

describe('AllNodes — BaseWorkflowNode-backed node types', () => {
  const cases: Array<[React.ComponentType<never>, string, string]> = [
    [ToolNode as never, 'tool', 'MCP Tool'],
    [LLMNode as never, 'llm', 'LLM Prompt'],
    [RAGNode as never, 'rag', 'RAG Retrieval'],
    [HTTPNode as never, 'http', 'HTTP Request'],
    [HITLNode as never, 'hitl', 'Human Review'],
    [ForeachNode as never, 'foreach', 'For Each'],
    [TransformNode as never, 'transform', 'Transform'],
    [SubWorkflowNode as never, 'sub_workflow', 'Sub-Workflow'],
    [CodeNode as never, 'code', 'Code'],
    [SetVariableNode as never, 'set_variable', 'Set Variable'],
    [EmitEventNode as never, 'emit_event', 'Emit Event'],
    [OcrNode as never, 'ocr', 'OCR Extract'],
    [RpaNode as never, 'rpa', 'Web Automation'],
  ];

  test.each(cases)('renders the type header for %o', (Node, stepType, displayName) => {
    renderNode(Node, { label: `${stepType}-label` });
    // Header shows the display name; body shows the custom label.
    expect(screen.getByText(displayName)).toBeInTheDocument();
    expect(screen.getByText(`${stepType}-label`)).toBeInTheDocument();
    expect(screen.getByLabelText(`Workflow step: ${stepType}-label (${stepType})`)).toBeInTheDocument();
  });

  test('unknown org-flow types fall back to the raw step type as their header', () => {
    renderNode(OrgDecisionNode as never, {});
    expect(screen.getAllByText('org_decision').length).toBeGreaterThanOrEqual(1);
    renderNode(DepartmentHandoffNode as never, {});
    expect(screen.getAllByText('department_handoff').length).toBeGreaterThanOrEqual(1);
    renderNode(CrossTeamReviewNode as never, {});
    expect(screen.getAllByText('cross_team_review').length).toBeGreaterThanOrEqual(1);
    renderNode(ParallelDepartmentsNode as never, {});
    expect(screen.getAllByText('parallel_departments').length).toBeGreaterThanOrEqual(1);
  });

  test('renders a subtitle and an error alert when the node has an error', () => {
    renderNode(ToolNode as never, {
      label: 'Call Jira',
      subtitle: 'jira_create_issue',
      hasError: true,
      errorMessage: 'Missing tool',
    });
    expect(screen.getByText('jira_create_issue')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Missing tool');
  });
});

describe('AllNodes — registry', () => {
  test('workflowNodeTypes maps every supported step type to a component', () => {
    const expectedKeys = [
      'trigger', 'tool', 'llm', 'rag', 'http', 'ocr', 'rpa', 'conditional', 'parallel',
      'hitl', 'foreach', 'transform', 'sub_workflow', 'wait', 'code', 'set_variable',
      'emit_event', 'org_decision', 'department_handoff', 'cross_team_review',
      'parallel_departments',
    ];
    for (const key of expectedKeys) {
      expect(typeof workflowNodeTypes[key as keyof typeof workflowNodeTypes]).toBe('function');
    }
    expect(Object.keys(workflowNodeTypes).length).toBe(expectedKeys.length);
  });
});
