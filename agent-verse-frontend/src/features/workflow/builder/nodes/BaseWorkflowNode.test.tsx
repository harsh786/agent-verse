import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';

// @xyflow/react's Handle needs the ReactFlow provider/DOM which jsdom lacks;
// stub the runtime pieces this node imports.
vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
}));

// framer-motion drives an infinite pulse for running nodes — stub motion.* so
// tests render plain DOM without animation timers.
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

import { BaseWorkflowNode } from './BaseWorkflowNode';

const AnyBaseWorkflowNode = BaseWorkflowNode as React.ElementType;

function renderNode(id: string, data: Record<string, unknown>, selected = false) {
  return render(
    // The component only reads id/data/selected from NodeProps.
    <AnyBaseWorkflowNode {...({ id, data, selected } as Record<string, unknown>)} />,
  );
}

afterEach(() => vi.restoreAllMocks());

describe('BaseWorkflowNode', () => {
  test('renders the custom label and the type display name for a known type', () => {
    renderNode('n1', { stepType: 'llm', label: 'Draft copy' });
    expect(screen.getByText('Draft copy')).toBeInTheDocument();
    // Type header uses NODE_LABELS.
    expect(screen.getByText('LLM Prompt')).toBeInTheDocument();
    expect(screen.getByLabelText('Workflow step: Draft copy (llm)')).toBeInTheDocument();
  });

  test('falls back to the type display name when no label is provided', () => {
    renderNode('n2', { stepType: 'tool' });
    // Both the header and the body fall back to "MCP Tool".
    expect(screen.getAllByText('MCP Tool').length).toBeGreaterThanOrEqual(2);
  });

  test('an unknown step type falls back to the raw type as its label', () => {
    renderNode('n3', { stepType: 'weird' });
    expect(screen.getAllByText('weird').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByLabelText('Workflow step: weird (weird)')).toBeInTheDocument();
  });

  test('shows a running status badge when runStatus is running', () => {
    renderNode('n4', { stepType: 'llm', label: 'Busy', runStatus: 'running' });
    expect(screen.getByLabelText('Status: running')).toBeInTheDocument();
  });

  test('shows a subtitle and an error alert when the node has an error', () => {
    renderNode('n5', {
      stepType: 'http',
      label: 'Call API',
      subtitle: 'https://api.example.com',
      hasError: true,
      errorMessage: 'Missing URL',
    });
    expect(screen.getByText('https://api.example.com')).toBeInTheDocument();
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Missing URL');
  });

  test('right-clicking the card invokes onContextMenu with the node id', () => {
    const onContextMenu = vi.fn();
    renderNode('n6', { stepType: 'llm', label: 'Ctx', onContextMenu });
    fireEvent.contextMenu(screen.getByLabelText('Workflow step: Ctx (llm)'));
    expect(onContextMenu).toHaveBeenCalledTimes(1);
    expect(onContextMenu.mock.calls[0][1]).toBe('n6');
  });
});
