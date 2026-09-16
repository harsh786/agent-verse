import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import React, { type ReactNode } from 'react';
import { AutonomyControl } from './AutonomyControl';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

describe('AutonomyControl', () => {
  test('renders the current level label and description', () => {
    render(<AutonomyControl value={2} onChange={vi.fn()} />);
    expect(screen.getByText('Balanced')).toBeInTheDocument();
    expect(screen.getByText('Auto-execute routine tasks only')).toBeInTheDocument();
    // the current step button is pressed
    expect(screen.getByRole('button', { name: /Set autonomy to Balanced/i })).toHaveAttribute('aria-pressed', 'true');
  });

  test('clicking a step button reports the chosen level', async () => {
    const onChange = vi.fn();
    render(<AutonomyControl value={0} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: /Set autonomy to Autonomous/i }));
    expect(onChange).toHaveBeenCalledWith(4);
  });

  test('does not fire onChange while disabled', async () => {
    const onChange = vi.fn();
    render(<AutonomyControl value={1} onChange={onChange} disabled />);
    const btn = screen.getByRole('button', { name: /Set autonomy to Proactive/i });
    expect(btn).toBeDisabled();
    await userEvent.click(btn);
    expect(onChange).not.toHaveBeenCalled();
  });

  test('warns for proactive mode and escalates the copy at full autonomy', () => {
    const { rerender } = render(<AutonomyControl value={3} onChange={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent(/Proactive mode/i);

    rerender(<AutonomyControl value={4} onChange={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent(/Fully autonomous/i);
  });

  test('shows no risk warning for low autonomy levels', () => {
    render(<AutonomyControl value={1} onChange={vi.fn()} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
