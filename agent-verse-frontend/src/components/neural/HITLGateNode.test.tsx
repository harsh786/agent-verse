/**
 * HITLGateNode — amber approval gate node. Verifies the waiting countdown
 * (mm:ss derived from timeoutMs), the approved/rejected labels + aria-label,
 * the urgent (<60s) branch, and reduced-motion collapse of the pulse ring.
 */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, it, expect, vi } from 'vitest';
import { HITLGateNode } from './HITLGateNode';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return { ...actual, useReducedMotion: () => (globalThis as { __reduceMotion?: boolean }).__reduceMotion ?? false };
});

function setReduce(v: boolean) {
  (globalThis as { __reduceMotion?: boolean }).__reduceMotion = v;
}

afterEach(() => {
  setReduce(false);
  vi.restoreAllMocks();
});

describe('HITLGateNode', () => {
  it('waiting: renders the label, the mm:ss countdown, the action and approver', () => {
    render(
      <HITLGateNode status="waiting" timeoutMs={125_000} action="deploy prod api" approver="Ada" />,
    );
    expect(screen.getByText('Awaiting approval')).toBeInTheDocument();
    // 125000ms -> 2:05
    expect(screen.getByText('2:05')).toBeInTheDocument();
    expect(screen.getByText('remaining')).toBeInTheDocument();

    const action = screen.getByText('deploy prod api');
    expect(action).toBeInTheDocument();
    expect(action).toHaveClass('font-mono');
    expect(screen.getByText('Approver: Ada')).toBeInTheDocument();
  });

  it('waiting: exposes role=status with the aria-label for the current status', () => {
    render(<HITLGateNode status="waiting" timeoutMs={300_000} />);
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', 'Approval gate: waiting');
  });

  it('approved: renders the approved label + aria-label and no remaining countdown', () => {
    render(<HITLGateNode status="approved" timeoutMs={125_000} />);
    expect(screen.getByText('Approved ✓')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', 'Approval gate: approved');
    expect(screen.queryByText('remaining')).not.toBeInTheDocument();
    expect(screen.queryByText('2:05')).not.toBeInTheDocument();
  });

  it('rejected: renders the rejected label + aria-label and no countdown', () => {
    render(<HITLGateNode status="rejected" timeoutMs={125_000} />);
    expect(screen.getByText('Rejected ✗')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', 'Approval gate: rejected');
    expect(screen.queryByText('remaining')).not.toBeInTheDocument();
  });

  it('urgent (<60s): still renders a padded mm:ss countdown', () => {
    render(<HITLGateNode status="waiting" timeoutMs={30_000} />);
    // 30000ms -> 0:30
    expect(screen.getByText('0:30')).toBeInTheDocument();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('reduced motion: renders the waiting node and omits the amber pulse ring', () => {
    setReduce(true);
    const { container } = render(<HITLGateNode status="waiting" timeoutMs={300_000} />);
    expect(screen.getByText('Awaiting approval')).toBeInTheDocument();
    // The pulse ring is the only inset-0 rounded-xl bordered overlay.
    expect(container.querySelectorAll('.absolute.inset-0.rounded-xl.border')).toHaveLength(0);
  });

  it('waiting with motion: renders the amber pulse ring', () => {
    setReduce(false);
    const { container } = render(<HITLGateNode status="waiting" timeoutMs={300_000} />);
    expect(container.querySelectorAll('.absolute.inset-0.rounded-xl.border')).toHaveLength(1);
  });
});
