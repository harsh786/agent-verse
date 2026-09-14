/**
 * CommandBar mission-preview tests (FE1).
 *
 * The Step 2 "preview" used to show fabricated cost/risk/agent-count
 * numbers from Math.random(). No pre-submit mission preview/estimate
 * endpoint exists on the backend (see CommandBar.tsx TODO(api) note), so
 * these tests verify the preview step shows the user's real refined goal
 * plus an honest "not estimated yet" placeholder — never fake numbers.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import { CommandBar } from '../CommandBar';

// ─── Mock framer-motion (animation timing is irrelevant to these assertions) ─
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => key in target ? target[key] : makeStub(key),
    }),
  };
});

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

function goToPreviewStep(goalText = 'Launch a new product line') {
  wrap(<CommandBar orgId="org-001" onClose={vi.fn()} />);
  const input = screen.getByLabelText('Command input');
  fireEvent.change(input, { target: { value: goalText } });
  // Submit via the first suggestion option (same handleSubmit as Enter).
  fireEvent.click(screen.getAllByRole('option')[0]);
}

describe('CommandBar mission preview (FE1)', () => {
  it('shows the real refined goal on the preview step', () => {
    goToPreviewStep('Launch a new product line');
    expect(screen.getByText('Mission Preview')).toBeInTheDocument();
    expect(screen.getByText('Launch a new product line')).toBeInTheDocument();
  });

  it('never renders fabricated cost, agent-count, or risk numbers', () => {
    goToPreviewStep();
    // These labels only existed alongside the Math.random()-based stats grid.
    expect(screen.queryByText('Agents')).not.toBeInTheDocument();
    expect(screen.queryByText('Est. cost')).not.toBeInTheDocument();
    expect(screen.queryByText('Risk')).not.toBeInTheDocument();
    expect(screen.queryByText('Departments involved')).not.toBeInTheDocument();
    expect(screen.queryByText('Success probability')).not.toBeInTheDocument();
    expect(screen.queryByText(/^\$\d/)).not.toBeInTheDocument();
  });

  it('shows an honest placeholder instead of fabricated numbers', () => {
    goToPreviewStep();
    // No pre-submit estimate endpoint result is fabricated: the preview shows an
    // honest state — either the in-flight "Estimating…" indicator or the
    // launch-time placeholder — never invented cost/agent/risk figures.
    expect(
      screen.getByText(/Estimating team, cost, and risk|size and dispatch the mission when you launch/i),
    ).toBeInTheDocument();
  });
});
