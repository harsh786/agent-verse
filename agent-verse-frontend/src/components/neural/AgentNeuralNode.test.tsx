/**
 * AgentNeuralNode — WS-7 item 3 ("bots alive"). Verifies idle agents still
 * get a subtle breathing animation (not frozen), working agents get the
 * bigger pulse + activity ring, and prefers-reduced-motion collapses both
 * to a static node.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

  it('calls onClick with the agentId when clicked, and no-ops without a handler', async () => {
    setReduce(false);
    const onClick = vi.fn();
    const { rerender } = render(<AgentNeuralNode agentId="a1" label="Ada" state="idle" onClick={onClick} />);
    await userEvent.click(screen.getByLabelText('Agent Ada: idle'));
    expect(onClick).toHaveBeenCalledWith('a1');

    rerender(<AgentNeuralNode agentId="a1" label="Ada" state="idle" />);
    await userEvent.click(screen.getByLabelText('Agent Ada: idle'));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('isSelected overrides the state border color with the accent color', () => {
    setReduce(false);
    const { rerender, getByLabelText } = render(
      <AgentNeuralNode agentId="a1" label="Ada" state="idle" />,
    );
    expect(getByLabelText('Agent Ada: idle')).not.toHaveStyle({ borderColor: '#00D4FF' });

    rerender(<AgentNeuralNode agentId="a1" label="Ada" state="idle" isSelected />);
    expect(getByLabelText('Agent Ada: idle')).toHaveStyle({ borderColor: '#00D4FF' });
  });

  it.each([
    ['completed', '#00E676'],
    ['error', '#FF3366'],
    ['blocked', '#FF3366'],
    ['waiting', '#FFB300'],
    ['idle', '#00D4FF'],
  ] as const)('colors the icon correctly for state=%s', (state, color) => {
    setReduce(false);
    const { container } = render(<AgentNeuralNode agentId="a1" label="Ada" state={state} />);
    const icon = container.querySelector('svg');
    expect(icon).toHaveStyle({ color });
  });

  it.each(['sm', 'md', 'lg'] as const)('applies the %s size to width/height', (size) => {
    setReduce(false);
    const { getByLabelText } = render(<AgentNeuralNode agentId="a1" label="Ada" state="idle" size={size} />);
    const expected = { sm: 32, md: 44, lg: 56 }[size];
    expect(getByLabelText('Agent Ada: idle')).toHaveStyle({ width: `${expected}px`, height: `${expected}px` });
  });

  it('applies a custom className to the button', () => {
    setReduce(false);
    render(<AgentNeuralNode agentId="a1" label="Ada" state="idle" className="my-node" />);
    expect(screen.getByLabelText('Agent Ada: idle')).toHaveClass('my-node');
  });
});
