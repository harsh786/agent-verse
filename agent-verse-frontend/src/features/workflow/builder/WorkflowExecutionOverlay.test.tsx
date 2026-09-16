import { render, screen } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, test, vi } from 'vitest';

// @xyflow/react's Panel needs the ReactFlow provider; stub it to a plain div.
vi.mock('@xyflow/react', () => ({
  Panel: ({ children, ...props }: { children?: React.ReactNode; [k: string]: unknown }) =>
    React.createElement('div', props as Record<string, unknown>, children),
}));

import { WorkflowExecutionOverlay } from './WorkflowExecutionOverlay';

afterEach(() => vi.restoreAllMocks());

describe('WorkflowExecutionOverlay', () => {
  test('renders nothing when there are no steps', () => {
    render(<WorkflowExecutionOverlay stepStatuses={{}} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  test('shows the running count when steps are running', () => {
    render(<WorkflowExecutionOverlay stepStatuses={{ a: 'running', b: 'running', c: 'pending' }} />);
    expect(screen.getByText(/2 running/)).toBeInTheDocument();
  });

  test('shows the complete/total ratio in the accessible label', () => {
    render(<WorkflowExecutionOverlay stepStatuses={{ a: 'complete', b: 'complete', c: 'running' }} />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-label', 'Execution status: 2/3 steps complete');
    expect(screen.getByText('2/3')).toBeInTheDocument();
  });

  test('shows the failed count when steps have failed', () => {
    render(<WorkflowExecutionOverlay stepStatuses={{ a: 'failed', b: 'failed', c: 'complete' }} />);
    expect(screen.getByText(/2 failed/)).toBeInTheDocument();
  });

  test('hides the running segment when nothing is running', () => {
    render(<WorkflowExecutionOverlay stepStatuses={{ a: 'complete', b: 'complete' }} />);
    expect(screen.queryByText(/running/)).not.toBeInTheDocument();
    expect(screen.getByText('2/2')).toBeInTheDocument();
  });

  test('renders all three segments together when the run has a mix of statuses', () => {
    render(
      <WorkflowExecutionOverlay
        stepStatuses={{ a: 'running', b: 'complete', c: 'failed', d: 'pending' }}
      />,
    );
    expect(screen.getByText(/1 running/)).toBeInTheDocument();
    expect(screen.getByText('1/4')).toBeInTheDocument();
    expect(screen.getByText(/1 failed/)).toBeInTheDocument();
  });
});
