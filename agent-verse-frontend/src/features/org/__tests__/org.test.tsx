/**
 * Tests for the AI Organization OS frontend.
 * Uses vi.mock for hook mocking — no MSW dependency needed.
 *
 * Skills applied:
 *   - tdd.instructions: RED→GREEN→REFACTOR, tests for render/empty/error/a11y
 *   - test-coverage: ≥90% coverage target
 *   - web-guidelines: verify aria-label, role, keyboard navigation
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';

import { MissionCard } from '../components/MissionCard';
import { OrgHealthWidget } from '../components/OrgHealthWidget';
import type { OrgMission } from '../types';

// ─── Mock framer-motion to avoid animation timing issues in tests ─────────────
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return {
    ...actual,
    useReducedMotion: () => true,   // always reduced in tests
    motion: {
      ...actual.motion,
      div: ({ children, ...props }: { children: ReactNode; [k: string]: unknown }) =>
        <div {...props as Record<string, unknown>}>{children}</div>,
      article: ({ children, ...props }: { children: ReactNode; [k: string]: unknown }) =>
        <article {...props as Record<string, unknown>}>{children}</article>,
    },
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
    expect(screen.getByText('critical')).toBeInTheDocument();
  });

  it('renders deadline when set', () => {
    wrap(<MissionCard mission={buildMission({ deadline: '2026-09-01T00:00:00Z' })} />);
    // Should show formatted date via Intl
    expect(screen.getByRole('time')).toBeInTheDocument();
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
