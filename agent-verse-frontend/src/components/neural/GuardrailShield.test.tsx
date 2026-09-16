import { render, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

// Keep real motion/AnimatePresence (they render children synchronously in
// jsdom) but make useReducedMotion deterministic per-test.
const reduce = vi.fn(() => false);
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return { ...actual, useReducedMotion: () => reduce() };
});

import { GuardrailShield } from './GuardrailShield';

beforeEach(() => reduce.mockReturnValue(false));
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  reduce.mockReturnValue(false);
});

describe('GuardrailShield', () => {
  test('renders nothing while not visible', () => {
    const { container } = render(<GuardrailShield visible={false} ruleName="no-prod-deletes" />);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(screen.queryByText(/Blocked:/)).not.toBeInTheDocument();
  });

  test('renders the alert region and the blocked rule name when visible', () => {
    render(<GuardrailShield visible ruleName="no-prod-deletes" />);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveAttribute('aria-live', 'assertive');
    expect(screen.getByText('Blocked: no-prod-deletes')).toBeInTheDocument();
  });

  test('truncates a long rule name to 40 characters', () => {
    const long = 'x'.repeat(60);
    render(<GuardrailShield visible ruleName={long} />);
    expect(screen.getByText(`Blocked: ${'x'.repeat(40)}`)).toBeInTheDocument();
  });

  test('omits the rule-name label when no ruleName is given', () => {
    render(<GuardrailShield visible />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.queryByText(/Blocked:/)).not.toBeInTheDocument();
  });

  test('forwards a custom className onto the alert wrapper', () => {
    render(<GuardrailShield visible ruleName="r" className="my-extra-class" />);
    expect(screen.getByRole('alert').className).toContain('my-extra-class');
  });

  test('does not render the burst rings under reduced motion', () => {
    reduce.mockReturnValue(true);
    const { container } = render(<GuardrailShield visible ruleName="r" />);
    // The three CSS burst rings use the .absolute.rounded-full.border classes;
    // reduced motion suppresses them.
    expect(container.querySelectorAll('.absolute.rounded-full.border').length).toBe(0);
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  test('renders the burst rings with full motion enabled', () => {
    reduce.mockReturnValue(false);
    const { container } = render(<GuardrailShield visible ruleName="r" />);
    expect(container.querySelectorAll('.absolute.rounded-full.border').length).toBe(3);
  });

  test('dims the shield after the 5s timeout elapses', () => {
    vi.useFakeTimers();
    const { container } = render(<GuardrailShield visible ruleName="r" />);
    // Before the timer: burst rings present (not dimmed).
    expect(container.querySelectorAll('.absolute.rounded-full.border').length).toBe(3);
    act(() => vi.advanceTimersByTime(5000));
    // After dimming, the burst rings are removed (dimmed branch).
    expect(container.querySelectorAll('.absolute.rounded-full.border').length).toBe(0);
  });
});
