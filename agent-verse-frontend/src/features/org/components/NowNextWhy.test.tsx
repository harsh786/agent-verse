import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { NowNextWhy } from './NowNextWhy';
import type { OrgMission } from '../types';

// The component only reads id/title/status/priority off a mission; build minimal
// stand-ins and cast rather than filling the full OrgMission shape.
function mission(partial: Partial<OrgMission>): OrgMission {
  return partial as OrgMission;
}

const HEALTH = {
  health: 'healthy',
  active_missions: 3,
  active_teams: 4,
  pending_approvals: 2,
  task_counts: { running: 5, blocked: 1 },
  event_counts_24h: {},
  items_needing_attention: 1,
};

afterEach(() => vi.restoreAllMocks());

describe('NowNextWhy', () => {
  test('renders the loading state when isLoading is set', () => {
    render(<NowNextWhy isLoading />);
    expect(screen.getByText(/Loading…/)).toBeInTheDocument();
  });

  test('NOW panel shows health stat labels/values and the active mission list', () => {
    render(
      <NowNextWhy
        health={HEALTH}
        missions={[
          mission({ id: 'm1', title: 'Migrate billing', status: 'active', priority: 'critical' }),
          mission({ id: 'm2', title: 'Refresh docs', status: 'active', priority: 'low' }),
          mission({ id: 'm3', title: 'Done thing', status: 'completed', priority: 'low' }),
        ]}
      />,
    );
    expect(screen.getByText('Missions')).toBeInTheDocument();
    expect(screen.getByText('Tasks Running')).toBeInTheDocument();
    expect(screen.getByText('Blocked')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument(); // running count
    expect(screen.getByText('Active Missions')).toBeInTheDocument();
    expect(screen.getByText('Migrate billing')).toBeInTheDocument();
    expect(screen.getByText('Refresh docs')).toBeInTheDocument();
    // A completed mission is not part of the active list.
    expect(screen.queryByText('Done thing')).not.toBeInTheDocument();
  });

  test('NOW panel shows the empty state when no mission is active', () => {
    render(
      <NowNextWhy
        health={HEALTH}
        missions={[mission({ id: 'm3', title: 'Done thing', status: 'completed', priority: 'low' })]}
      />,
    );
    expect(screen.getByText(/No missions running right now/i)).toBeInTheDocument();
  });

  test('selecting the NEXT tab reveals the approvals banner and upcoming items', () => {
    render(
      <NowNextWhy
        health={HEALTH}
        upcoming={[
          { id: 'u1', label: 'Contract renewal', urgency: 'high', type: 'deadline' },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: /NEXT/i }));
    expect(screen.getByText(/2 approvals awaiting/i)).toBeInTheDocument();
    expect(screen.getByText('Contract renewal')).toBeInTheDocument();
    expect(screen.getByText('deadline')).toBeInTheDocument();
  });

  test('NEXT tab shows the empty state when nothing is upcoming', () => {
    render(<NowNextWhy health={{ ...HEALTH, pending_approvals: 0 }} upcoming={[]} />);
    fireEvent.click(screen.getByRole('tab', { name: /NEXT/i }));
    expect(screen.getByText(/Nothing urgent coming up/i)).toBeInTheDocument();
  });

  test('WHY tab renders decisions, and its empty state when there are none', () => {
    const { rerender } = render(
      <NowNextWhy
        decisions={[
          { id: 'd1', action: 'Paused overspending mission', reason: 'Budget ceiling hit', autonomy: 'L3' },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: /WHY/i }));
    expect(screen.getByText('Paused overspending mission')).toBeInTheDocument();
    expect(screen.getByText('Budget ceiling hit')).toBeInTheDocument();
    expect(screen.getByText('L3')).toBeInTheDocument();

    rerender(<NowNextWhy decisions={[]} />);
    fireEvent.click(screen.getByRole('tab', { name: /WHY/i }));
    expect(screen.getByText(/No autonomous decisions in the last 24h/i)).toBeInTheDocument();
  });
});
