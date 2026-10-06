/**
 * Tests for ChatHITLCard — the human-in-the-loop approval card.
 */
import { render, screen, fireEvent, act } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ChatHITLCard } from './ChatHITLCard';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('ChatHITLCard', () => {
  test('renders the step, risk level, request id and initial countdown', () => {
    render(<ChatHITLCard stepName="Deploy to prod" riskLevel="critical" timeoutSeconds={300} requestId="req-99" />);
    expect(screen.getByText('Human approval required')).toBeInTheDocument();
    expect(screen.getByText('Deploy to prod')).toBeInTheDocument();
    expect(screen.getByText(/critical risk/i)).toBeInTheDocument();
    expect(screen.getByText(/ID: req-99/)).toBeInTheDocument();
    expect(screen.getByText('Expires in 5m 00s')).toBeInTheDocument();
  });

  test('fires onApprove and onReject when the buttons are clicked', () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(<ChatHITLCard stepName="s" onApprove={onApprove} onReject={onReject} />);
    fireEvent.click(screen.getByRole('button', { name: 'Approve action' }));
    fireEvent.click(screen.getByRole('button', { name: 'Reject action' }));
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  test('the live countdown ticks down each second', () => {
    vi.useFakeTimers();
    render(<ChatHITLCard stepName="s" timeoutSeconds={125} />);
    expect(screen.getByText('Expires in 2m 05s')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(6000));
    expect(screen.getByText('Expires in 1m 59s')).toBeInTheDocument();
  });

  test('shows "Timed out" once the countdown reaches zero', () => {
    vi.useFakeTimers();
    render(<ChatHITLCard stepName="s" timeoutSeconds={3} />);
    act(() => vi.advanceTimersByTime(4000));
    expect(screen.getByText('Timed out')).toBeInTheDocument();
  });

  test('renders an urgent (red) expiry label when under a minute remains', () => {
    render(<ChatHITLCard stepName="s" timeoutSeconds={45} />);
    const label = screen.getByText('Expires in 45s');
    expect(label.className).toMatch(/text-red-500/);
  });

  test('offers no unsigned magic link, even for an event carrying a token (a03-F056-06)', () => {
    // The old card built /hitl/:id/approve?token=<uuid>, which the signed
    // (sig + exp) decision page rejects.
    const legacyEvent = { approvalToken: 'tok-abc' } as Record<string, unknown>;
    render(<ChatHITLCard stepName="s" requestId="req-1" {...legacyEvent} />);
    expect(screen.queryByRole('button', { name: /magic approve link/i })).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain('/hitl/req-1/approve?token=');
  });
});
