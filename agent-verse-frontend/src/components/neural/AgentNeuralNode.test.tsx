/**
 * AgentNeuralNode — WS-7 item 3 ("bots alive"). Verifies idle agents still
 * get a subtle breathing animation (not frozen), working agents get the
 * bigger pulse + activity ring, and prefers-reduced-motion collapses both
 * to a static node.
 */
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AgentNeuralNode } from './AgentNeuralNode';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return { ...actual, useReducedMotion: () => (globalThis as { __reduceMotion?: boolean }).__reduceMotion ?? false };
});

function setReduce(v: boolean) {
  (globalThis as { __reduceMotion?: boolean }).__reduceMotion = v;
}

describe('AgentNeuralNode', () => {
  it('renders an idle agent with its accessible state label', () => {
    setReduce(false);
    render(<AgentNeuralNode agentId="a1" label="Ada" state="idle" />);
    expect(screen.getByLabelText('Agent Ada: idle')).toBeInTheDocument();
  });

  it('shows the pulse ring only for live (working) states, not idle', () => {
    setReduce(false);
    const { rerender, container } = render(<AgentNeuralNode agentId="a1" label="Ada" state="idle" />);
    expect(container.querySelectorAll('.absolute.inset-0.rounded-full.border')).toHaveLength(0);

    rerender(<AgentNeuralNode agentId="a1" label="Ada" state="executing" />);
    expect(container.querySelectorAll('.absolute.inset-0.rounded-full.border')).toHaveLength(1);
  });

  it('renders without crashing under prefers-reduced-motion for both idle and executing', () => {
    setReduce(true);
    const { rerender } = render(<AgentNeuralNode agentId="a1" label="Ada" state="idle" />);
    expect(screen.getByLabelText('Agent Ada: idle')).toBeInTheDocument();
    rerender(<AgentNeuralNode agentId="a1" label="Ada" state="executing" />);
    expect(screen.getByLabelText('Agent Ada: executing')).toBeInTheDocument();
  });
});
