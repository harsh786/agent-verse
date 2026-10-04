/**
 * CommandBar mission-preview tests (FE1).
 *
 * The Step 2 "preview" used to show fabricated cost/risk/agent-count
 * numbers from Math.random(). The preview now asks the backend for a
 * pre-flight estimate (orgApi.previewMission); when none is available it
 * shows the user's real refined goal plus an honest placeholder — never
 * fake numbers.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import { CommandBar } from '../CommandBar';
import { orgApi } from '../api';

// The pre-flight estimate is a real backend call. Mocked so no request outlives
// the test: an unmocked call settled after jsdom teardown and its setState threw
// "window is not defined" as an unhandled rejection (failing the vitest run).
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    orgApi: { ...actual.orgApi, previewMission: vi.fn() },
  };
});

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

async function goToPreviewStep(goalText = 'Launch a new product line') {
  // No estimate available (backend heuristic unreachable).
  vi.mocked(orgApi.previewMission).mockRejectedValue(new Error('estimate unavailable'));
  wrap(<CommandBar orgId="org-001" onClose={vi.fn()} />);
  const input = screen.getByLabelText('Command input');
  fireEvent.change(input, { target: { value: goalText } });
  // Submit via the first suggestion option (same handleSubmit as Enter).
  fireEvent.click(screen.getAllByRole('option')[0]);
  // Let the estimate request settle inside the test.
  await waitFor(() =>
    expect(screen.queryByText(/Estimating team, cost, and risk/i)).not.toBeInTheDocument(),
  );
}

describe('CommandBar mission preview (FE1)', () => {
  it('shows the real refined goal on the preview step', async () => {
    await goToPreviewStep('Launch a new product line');
    expect(screen.getByText('Mission Preview')).toBeInTheDocument();
    expect(screen.getByText('Launch a new product line')).toBeInTheDocument();
  });

  it('never renders fabricated cost, agent-count, or risk numbers', async () => {
    await goToPreviewStep();
    // These labels only existed alongside the Math.random()-based stats grid.
    expect(screen.queryByText('Agents')).not.toBeInTheDocument();
    expect(screen.queryByText('Est. cost')).not.toBeInTheDocument();
    expect(screen.queryByText('Risk')).not.toBeInTheDocument();
    expect(screen.queryByText('Departments involved')).not.toBeInTheDocument();
    expect(screen.queryByText('Success probability')).not.toBeInTheDocument();
    expect(screen.queryByText(/^\$\d/)).not.toBeInTheDocument();
  });

  it('shows an honest placeholder when no estimate is available', async () => {
    await goToPreviewStep();
    expect(
      screen.getByText(/size and dispatch the mission when you launch/i),
    ).toBeInTheDocument();
    expect(orgApi.previewMission).toHaveBeenCalledWith('org-001', 'Launch a new product line');
  });
});
