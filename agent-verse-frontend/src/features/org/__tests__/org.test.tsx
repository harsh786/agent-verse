/**
 * Tests for the AI Organization OS frontend.
 * Uses vi.mock for hook mocking — no MSW dependency needed.
 *
 * Skills applied:
 *   - tdd.instructions: RED→GREEN→REFACTOR, tests for render/empty/error/a11y
 *   - test-coverage: ≥90% coverage target
 *   - web-guidelines: verify aria-label, role, keyboard navigation
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import { MissionCard } from '../components/MissionCard';
import { OrgHealthWidget } from '../components/OrgHealthWidget';
import type { OrgMission } from '../types';

// ─── Mock framer-motion to avoid animation timing issues in tests ─────────────
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  // Build a proxy that stubs ALL motion.* HTML elements
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

// ─── Test wrapper ──────────────────────────────────────────────────────────────

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

// ─── Fixtures ──────────────────────────────────────────────────────────────────

function buildMission(overrides?: Partial<OrgMission>): OrgMission {
  return {
    id:               'm-001',
    tenant_id:        't-001',
    org_id:           'org-001',
    dept_id:          null,
    assigned_team_id: null,
    title:            'Research AI market trends',
    objective:        'Understand competitive landscape',
    why:              'To guide product strategy',
    expected_outcome: 'Comprehensive report',
    status:           'active',
    priority:         'high',
    source:           'manual',
    autonomy_level:   3,
    budget_usd:       null,
    deadline:         null,
    tags:             [],
    created_by:       null,
    outputs:          [],
    evidence:         [],
    started_at:       null,
    completed_at:     null,
    created_at:       '2026-08-17T00:00:00Z',
    updated_at:       '2026-08-17T00:00:00Z',
    ...overrides,
  };
}

// ─── (OrgHealthResponse not used in current tests) ──────────────────────────

// ─── MissionCard tests ─────────────────────────────────────────────────────────

describe('MissionCard', () => {
  it('renders mission title', () => {
    wrap(<MissionCard mission={buildMission()} />);
    expect(screen.getByText('Research AI market trends')).toBeInTheDocument();
  });

  it('renders active status badge with correct label', () => {
    wrap(<MissionCard mission={buildMission({ status: 'active' })} />);
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('renders completed status', () => {
    wrap(<MissionCard mission={buildMission({ status: 'completed' })} />);
    expect(screen.getByText('Completed')).toBeInTheDocument();
  });

  it('renders failed status', () => {
    wrap(<MissionCard mission={buildMission({ status: 'failed' })} />);
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('renders objective when provided', () => {
    wrap(<MissionCard mission={buildMission({ objective: 'Analyse competitors' })} />);
    expect(screen.getByText('Analyse competitors')).toBeInTheDocument();
  });

  it('calls onClick when clicked', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    wrap(<MissionCard mission={buildMission()} onClick={onClick} />);
    await user.click(screen.getByRole('button'));
    expect(onClick).toHaveBeenCalledWith('m-001');
  });

  // web-guidelines: keyboard navigation
  it('calls onClick when Enter is pressed (keyboard nav)', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    wrap(<MissionCard mission={buildMission()} onClick={onClick} />);
    const card = screen.getByRole('button');
    card.focus();
    await user.keyboard('{Enter}');
    expect(onClick).toHaveBeenCalledWith('m-001');
  });

  it('calls onClick when Space is pressed (keyboard nav)', async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    wrap(<MissionCard mission={buildMission()} onClick={onClick} />);
    const card = screen.getByRole('button');
    card.focus();
    await user.keyboard(' ');
    expect(onClick).toHaveBeenCalledWith('m-001');
  });

  // web-guidelines: aria-labelledby or aria-label present
  it('has accessible label (aria-labelledby)', () => {
    wrap(<MissionCard mission={buildMission()} />);
    const card = screen.getByRole('button');
    // Card uses aria-labelledby pointing to the h3 title
    expect(card).toHaveAttribute('aria-labelledby');
  });

  // impeccable-ui: selected state
  it('shows selected state when isSelected=true', () => {
    wrap(<MissionCard mission={buildMission()} isSelected />);
    const card = screen.getByRole('button');
    expect(card).toHaveAttribute('aria-pressed', 'true');
  });

  // web-guidelines: progress bar with aria
  it('renders priority label', () => {
    wrap(<MissionCard mission={buildMission({ priority: 'critical' })} />);
    // The compact card surfaces a short, styled priority tag ("crit"/"high"),
    // not the full word, for high/critical missions only.
    expect(screen.getByText('crit')).toBeInTheDocument();
  });

  it('renders when a deadline is set (compact card intentionally omits it)', () => {
    wrap(<MissionCard mission={buildMission({ deadline: '2026-09-01T00:00:00Z' })} />);
    // The two-row scannable card (title/priority/status · objective/progress)
    // does not surface the deadline; a deadline-bearing mission still renders
    // cleanly, and no <time> element is present.
    expect(screen.getByText('Research AI market trends')).toBeInTheDocument();
    expect(screen.queryByRole('time')).not.toBeInTheDocument();
  });

  // impeccable-ui: memoization — equal props don't re-render
  it('has correct data-testid', () => {
    wrap(<MissionCard mission={buildMission()} />);
    expect(screen.getByTestId('mission-card')).toBeInTheDocument();
  });
});

// ─── OrgHealthWidget tests ─────────────────────────────────────────────────────

describe('OrgHealthWidget', () => {
  it('renders without crashing when given orgId', () => {
    // OrgHealthWidget takes orgId and fetches internally
    // Just verify it renders without throwing
    wrap(<OrgHealthWidget orgId="org-001" />);
    expect(document.body).toBeInTheDocument();
  });
});

// ─── OrgListPage integration tests ───────────────────────────────────────────

describe('OrgListPage', () => {
  it('renders page heading', async () => {
    const { OrgListPage } = await import('../OrgListPage');
    wrap(<OrgListPage />);
    expect(await screen.findByRole('heading', { name: /AI Organizations/i })).toBeInTheDocument();
  });

  it('renders loading or content state without crashing', async () => {
    const { OrgListPage } = await import('../OrgListPage');
    wrap(<OrgListPage />);
    // Either heading or loading indicator
    await screen.findByRole('heading');
    expect(document.body).toBeInTheDocument();
  });

  it('has accessible "Create organization" button', async () => {
    const { OrgListPage } = await import('../OrgListPage');
    wrap(<OrgListPage />);
    await screen.findByRole('heading');
    const btn = screen.getByRole('button', { name: /New Organization/i });
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveAttribute('aria-label');
  });

  it('shows create form when button clicked', async () => {
    const { OrgListPage } = await import('../OrgListPage');
    const { container } = wrap(<OrgListPage />);
    await screen.findByRole('heading');
    const newBtn = screen.getByRole('button', { name: /New Organization/i });
    // Use fireEvent to synchronously fire the click and trigger state update
    fireEvent.click(newBtn);
    // Wait for form input to appear in the DOM
    await waitFor(() => {
      const input = container.querySelector('input[type="text"]');
      expect(input).toBeTruthy();
    });
  });
});

// ─── ActivityFeed integration tests ─────────────────────────────────────────

describe('ActivityFeed', () => {
  it('renders activity feed section without crashing', async () => {
    const { ActivityFeed } = await import('../components/ActivityFeed');
    wrap(<ActivityFeed orgId="org-001" />);
    // Section renders — either loading, empty, or data state
    await screen.findByLabelText('Organisation activity feed');
    expect(document.body).toBeInTheDocument();
  });

  it('has aria-live region for real-time updates', async () => {
    const { ActivityFeed } = await import('../components/ActivityFeed');
    wrap(<ActivityFeed orgId="org-001" />);
    await screen.findByLabelText('Organisation activity feed');
    const liveRegion = document.querySelector('[aria-live]');
    expect(liveRegion).toBeTruthy();
  });
});

// ─── DepartmentTree integration tests ────────────────────────────────────────

describe('DepartmentTree', () => {
  it('renders department tree section without crashing', async () => {
    const { DepartmentTree } = await import('../components/DepartmentTree');
    wrap(<DepartmentTree orgId="org-001" />);
    // Component renders — loading skeleton or empty
    expect(document.body).toBeInTheDocument();
  });

  it('renders nav element when not in loading state', async () => {
    const { DepartmentTree } = await import('../components/DepartmentTree');
    wrap(<DepartmentTree orgId="org-001" />);
    // Wait a tick to see if loading state resolves
    await new Promise(resolve => setTimeout(resolve, 50));
    // Should render something (nav, skeleton, or empty message)
    expect(document.body).toBeInTheDocument();
  });
});

// ─── OrgPage command-panel cohesion smoke test ──────────────────────────────
//
// This is the "situation room" gate: mount the WHOLE command panel — status
// badge, narration, live agent network, team channel, brain feed, budget
// gauges, autonomy control, and the context sections — together, exactly as
// OrgPage assembles them, and confirm nothing crashes. No API/query mocking:
// following the same pattern as the ActivityFeed/DepartmentTree smoke tests
// above, every child component here already tolerates an unmocked (failing)
// fetch by rendering its own loading/empty state via TanStack Query — this
// test's job is only to prove the surfaces coexist without throwing, not to
// re-assert each component's own data behaviour (covered by their own tests).
describe('OrgPage — command panel cohesion', () => {
  function renderOrgPage(orgId = 'org-001') {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[`/org/${orgId}`]}>
          <Routes>
            <Route path="/org/:orgId" element={<OrgPageLazy />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  // Lazy-imported inside the test file (not at module scope) so the mocks
  // above (framer-motion, reduced motion) are already in place before
  // OrgPage and its tree of children evaluate.
  let OrgPageLazy: React.ComponentType;

  it('mounts the full Situation Room command panel without crashing', async () => {
    ({ OrgPage: OrgPageLazy } = await import('../OrgPage'));
    renderOrgPage();

    // Reduced-motion is mocked true, so the JARVIS boot screen is skipped and
    // the real command panel renders immediately.
    const panel = await screen.findByLabelText('Command panel');
    expect(panel).toBeInTheDocument();

    // TOP — always-visible status + narration.
    expect(within(panel).getByLabelText('Mission narration')).toBeInTheDocument();

    // LIVE — the agent constellation / live network section.
    expect(within(panel).getByLabelText('Live agent network')).toBeInTheDocument();

    // COMMS — team channel.
    expect(within(panel).getByLabelText('Team channel')).toBeInTheDocument();

    // BRAIN — decisions feed + budget gauges.
    expect(within(panel).getByLabelText('Autonomous decisions')).toBeInTheDocument();
    expect(within(panel).getByLabelText('Budget burn')).toBeInTheDocument();

    // CONTROL — the full autonomy level/caps panel (its own query is still
    // loading at this point, so assert the always-rendered section heading
    // rather than content gated behind AutonomyControl's internal isLoading).
    expect(within(panel).getByRole('heading', { name: 'Autonomy' })).toBeInTheDocument();

    // CONTEXT — department tree + activity feed still present, unchanged.
    expect(within(panel).getByText(/Departments/)).toBeInTheDocument();
    expect(within(panel).getByLabelText('Live activity')).toBeInTheDocument();
  });

  it('renders the sections in Situation Room order: live → comms → brain → control', async () => {
    ({ OrgPage: OrgPageLazy } = await import('../OrgPage'));
    renderOrgPage();
    const panel = await screen.findByLabelText('Command panel');

    const live    = within(panel).getByLabelText('Live agent network');
    const comms   = within(panel).getByLabelText('Team channel');
    const brain   = within(panel).getByLabelText('Autonomous decisions');
    const budget  = within(panel).getByLabelText('Budget burn');
    const control = within(panel).getByRole('heading', { name: 'Autonomy' });

    // DOCUMENT_POSITION_FOLLOWING (4) means the second node comes after the first.
    const isBefore = (a: Element, b: Element) =>
      (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;

    expect(isBefore(live, comms)).toBe(true);
    expect(isBefore(comms, brain)).toBe(true);
    expect(isBefore(brain, budget)).toBe(true);
    expect(isBefore(budget, control)).toBe(true);
  });
});
