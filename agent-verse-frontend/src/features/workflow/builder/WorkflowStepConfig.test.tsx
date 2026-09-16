import { render, screen, fireEvent } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { WorkflowStepConfig } from './WorkflowStepConfig';

/** Grab the patch object most recently handed to onUpdate. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

function makeNode(stepType: string, data: Record<string, unknown> = {}, id = 'step-1'): Node {
  return {
    id,
    type: stepType,
    position: { x: 0, y: 0 },
    data: { stepType, ...data },
  } as unknown as Node;
}

function renderConfig(node: Node, onUpdate = vi.fn(), onClose = vi.fn()) {
  render(<WorkflowStepConfig node={node} onUpdate={onUpdate} onClose={onClose} />);
  return { onUpdate, onClose };
}

afterEach(() => vi.restoreAllMocks());

describe('WorkflowStepConfig — shell', () => {
  test('header shows the type label + the read-only step id, and Label edits call onUpdate', () => {
    const { onUpdate } = renderConfig(makeNode('llm', { label: 'Draft copy' }, 'node-42'));
    // Header uses the node-type display name.
    expect(screen.getByText('LLM Prompt')).toBeInTheDocument();
    // Read-only id footer.
    expect(screen.getByText('ID: node-42')).toBeInTheDocument();
    // The common Label field is seeded and editable.
    const label = screen.getByLabelText('Label');
    expect(label).toHaveValue('Draft copy');
    fireEvent.change(label, { target: { value: 'Renamed' } });
    expect(onUpdate).toHaveBeenCalledWith({ label: 'Renamed' });
  });

  test('close button invokes onClose', () => {
    const { onClose } = renderConfig(makeNode('tool'));
    fireEvent.click(screen.getByLabelText('Close config panel'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('an unknown step type renders the no-config hint', () => {
    renderConfig(makeNode('mystery-type'));
    expect(screen.getByText('No additional configuration for this step type.')).toBeInTheDocument();
  });
});

describe('WorkflowStepConfig — trigger', () => {
  test('default (api) trigger shows the manual/API note and can switch type', () => {
    const { onUpdate } = renderConfig(makeNode('trigger'));
    expect(screen.getByText(/Runs are started manually/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('How does this workflow start?'), {
      target: { value: 'schedule' },
    });
    expect(onUpdate).toHaveBeenCalledWith({ triggerType: 'schedule' });
  });

  test('schedule trigger humanizes the cron and applies presets', () => {
    const { onUpdate } = renderConfig(
      makeNode('trigger', { triggerType: 'schedule', cron: '0 9 * * *' }),
    );
    expect(screen.getByText('Every day at 9:00 AM')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hourly' }));
    expect(onUpdate).toHaveBeenCalledWith({ cron: '0 * * * *' });
  });
});

describe('WorkflowStepConfig — llm', () => {
  test('renders prompt/model/temperature/max-tokens/json fields and reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('llm'));
    expect(screen.getByLabelText('Prompt')).toBeInTheDocument();
    expect(screen.getByLabelText('Model')).toBeInTheDocument();
    expect(screen.getByLabelText('Temperature')).toBeInTheDocument();
    expect(screen.getByLabelText('Max tokens')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Summarize {{inputs.doc}}' } });
    expect(onUpdate).toHaveBeenCalledWith({ prompt: 'Summarize {{inputs.doc}}' });

    // The JSON output toggle flips from false → true.
    fireEvent.click(screen.getByRole('switch'));
    expect(onUpdate).toHaveBeenCalledWith({ json_output: true });
  });
});

describe('WorkflowStepConfig — tool', () => {
  test('renders the tool name + an (empty) arguments editor', () => {
    const { onUpdate } = renderConfig(makeNode('tool'));
    const tool = screen.getByLabelText('Tool name');
    expect(screen.getByText('Arguments')).toBeInTheDocument();
    expect(screen.getByText('No entries yet.')).toBeInTheDocument();
    fireEvent.change(tool, { target: { value: 'github.create_issue' } });
    expect(onUpdate).toHaveBeenCalledWith({ tool: 'github.create_issue' });
  });
});

describe('WorkflowStepConfig — http', () => {
  test('default POST shows URL/method/headers/auth and a request body editor', () => {
    const { onUpdate } = renderConfig(makeNode('http'));
    expect(screen.getByLabelText('URL')).toBeInTheDocument();
    expect(screen.getByLabelText('Method')).toHaveValue('POST');
    expect(screen.getByText('Headers')).toBeInTheDocument();
    expect(screen.getByText('Request body')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'PUT' } });
    expect(onUpdate).toHaveBeenCalledWith({ method: 'PUT' });
  });

  test('GET hides the body, and choosing bearer auth merges into the auth object', () => {
    const { onUpdate } = renderConfig(makeNode('http', { method: 'GET' }));
    expect(screen.queryByText('Request body')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Authentication'), { target: { value: 'bearer' } });
    expect(onUpdate).toHaveBeenCalledWith({ auth: { type: 'bearer' } });
  });

  test('with bearer auth already set, the token field is shown', () => {
    renderConfig(makeNode('http', { method: 'GET', auth: { type: 'bearer', token: 'abc' } }));
    expect(screen.getByLabelText('Token')).toHaveValue('abc');
  });
});

describe('WorkflowStepConfig — control flow & others', () => {
  test('hitl renders priority + escalation and reports a priority change', () => {
    const { onUpdate } = renderConfig(makeNode('hitl'));
    expect(screen.getByLabelText('Assignee role')).toBeInTheDocument();
    expect(screen.getByLabelText('Escalate after (hours)')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Priority'), { target: { value: 'high' } });
    expect(onUpdate).toHaveBeenCalledWith({ priority: 'high' });
  });

  test('conditional renders the expression field with the true/false handle note', () => {
    const { onUpdate } = renderConfig(makeNode('conditional'));
    expect(screen.getByText(/green \(True\) handle/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Expression'), {
      target: { value: '{{steps.risk.output.score}} > 0.7' },
    });
    expect(onUpdate).toHaveBeenCalledWith({ expression: '{{steps.risk.output.score}} > 0.7' });
  });

  test('code swaps its placeholder based on the selected runtime', () => {
    const { rerender } = render(
      <WorkflowStepConfig node={makeNode('code')} onUpdate={vi.fn()} onClose={vi.fn()} />,
    );
    expect(screen.getByLabelText('Code')).toHaveAttribute('placeholder', expect.stringContaining('output ='));
    rerender(
      <WorkflowStepConfig node={makeNode('code', { runtime: 'javascript' })} onUpdate={vi.fn()} onClose={vi.fn()} />,
    );
    expect(screen.getByLabelText('Code')).toHaveAttribute('placeholder', expect.stringContaining('return {'));
  });

  test('ocr writes a single source field into the nested input dict', () => {
    const { onUpdate } = renderConfig(makeNode('ocr'));
    fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://x/scan.pdf' } });
    expect(onUpdate).toHaveBeenCalledWith({ input: { url: 'https://x/scan.pdf' } });
  });

  test('sub_workflow renders the workflow id + inputs editor', () => {
    const { onUpdate } = renderConfig(makeNode('sub_workflow'));
    expect(screen.getByText('Inputs')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Workflow ID'), { target: { value: 'wf-123' } });
    expect(onUpdate).toHaveBeenCalledWith({ workflow_id: 'wf-123' });
  });

  test('parallel renders its branch explainer (no editable fields)', () => {
    renderConfig(makeNode('parallel'));
    expect(screen.getByText(/Runs its branch steps concurrently/i)).toBeInTheDocument();
  });
});

describe('WorkflowStepConfig — advanced section', () => {
  test('advanced fields are collapsed until expanded, then editable', () => {
    const { onUpdate } = renderConfig(makeNode('tool'));
    // Collapsed by default — the Timeout field is not mounted.
    expect(screen.queryByLabelText('Timeout')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Advanced/i }));
    const timeout = screen.getByLabelText('Timeout');
    expect(timeout).toBeInTheDocument();
    fireEvent.change(timeout, { target: { value: '60s' } });
    expect(onUpdate).toHaveBeenCalledWith({ timeout: '60s' });
  });
});
