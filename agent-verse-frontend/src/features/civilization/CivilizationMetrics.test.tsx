import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import React from 'react';

vi.mock('recharts', () => ({
  BarChart: ({ children }: { children?: React.ReactNode }) => <div data-testid="bar-chart">{children}</div>,
  Bar: ({ children }: { children?: React.ReactNode }) => <div data-testid="bar">{children}</div>,
  Cell: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

import { CivilizationMetrics } from './CivilizationMetrics';
import type { CivilizationMetrics as MetricsType } from '../../lib/api/civilizationApi';

function baseMetrics(overrides: Partial<MetricsType> = {}): MetricsType {
  return {
    total_members: 5,
    active_members: 3,
    idle_members: 1,
    retired_members: 1,
    total_budget_spent_usd: 12.5,
    avg_reputation: 0.7,
    max_reputation: 0.9,
    min_reputation: 0.4,
    ...overrides,
  } as MetricsType;
}

describe('CivilizationMetrics', () => {
  it('renders KPI values from provided metrics', () => {
    render(<CivilizationMetrics metrics={baseMetrics()} />);
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('70%')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('$12.50')).toBeInTheDocument();
  });

  it('falls back to defaults when optional metric fields are missing', () => {
    render(<CivilizationMetrics metrics={{} as MetricsType} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.getByText('$0.00')).toBeInTheDocument();
    // Idle/Retired block hidden since both default to 0
    expect(screen.queryByText('Idle')).not.toBeInTheDocument();
    expect(screen.queryByText('Retired')).not.toBeInTheDocument();
  });

  it('colors Avg Rep green when reputation is high (>60)', () => {
    render(<CivilizationMetrics metrics={baseMetrics({ avg_reputation: 0.75 })} />);
    const avgRepValue = screen.getByText('75%');
    expect(avgRepValue.className).toContain('text-green-400');
  });

  it('colors Avg Rep amber when reputation is mid (>30 and <=60)', () => {
    render(<CivilizationMetrics metrics={baseMetrics({ avg_reputation: 0.45 })} />);
    const avgRepValue = screen.getByText('45%');
    expect(avgRepValue.className).toContain('text-amber-400');
  });

  it('colors Avg Rep red when reputation is low (<=30)', () => {
    render(<CivilizationMetrics metrics={baseMetrics({ avg_reputation: 0.2 })} />);
    const avgRepValue = screen.getByText('20%');
    expect(avgRepValue.className).toContain('text-red-400');
  });

  it('shows the Idle/Retired block when idle_members > 0', () => {
    render(<CivilizationMetrics metrics={baseMetrics({ idle_members: 2, retired_members: 0 })} />);
    expect(screen.getByText('Idle')).toBeInTheDocument();
    const retiredLabel = screen.getByText('Retired');
    const retiredCard = retiredLabel.closest('div')?.parentElement as HTMLElement;
    expect(within(retiredCard).getByText('0')).toBeInTheDocument();
  });

  it('shows the Idle/Retired block when retired_members > 0 (idle is 0)', () => {
    render(<CivilizationMetrics metrics={baseMetrics({ idle_members: 0, retired_members: 4 })} />);
    const idleLabel = screen.getByText('Idle');
    const idleCard = idleLabel.closest('div')?.parentElement as HTMLElement;
    expect(within(idleCard).getByText('0')).toBeInTheDocument();
    expect(screen.getByText('Retired')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('falls back idle/retired counts to 0 when those fields are undefined', () => {
    render(
      <CivilizationMetrics
        metrics={baseMetrics({ idle_members: undefined, retired_members: 3 }) as MetricsType}
      />,
    );
    const idleLabel = screen.getByText('Idle');
    const idleCard = idleLabel.closest('div')?.parentElement as HTMLElement;
    expect(within(idleCard).getByText('0')).toBeInTheDocument();
  });

  it('falls back retired count to 0 when retired_members is undefined', () => {
    render(
      <CivilizationMetrics
        metrics={baseMetrics({ idle_members: 2, retired_members: undefined }) as MetricsType}
      />,
    );
    const retiredLabel = screen.getByText('Retired');
    const retiredCard = retiredLabel.closest('div')?.parentElement as HTMLElement;
    expect(within(retiredCard).getByText('0')).toBeInTheDocument();
  });

  it('hides the Idle/Retired block when both idle and retired members are 0', () => {
    render(<CivilizationMetrics metrics={baseMetrics({ idle_members: 0, retired_members: 0 })} />);
    expect(screen.queryByText('Idle')).not.toBeInTheDocument();
    expect(screen.queryByText('Retired')).not.toBeInTheDocument();
  });

  it('renders the Composition block when more than one breakdown bucket has members', () => {
    render(
      <CivilizationMetrics
        metrics={baseMetrics({ active_members: 3, idle_members: 2, retired_members: 0 })}
      />,
    );
    expect(screen.getByText('Composition')).toBeInTheDocument();
    expect(screen.getByText('Active (3)')).toBeInTheDocument();
    expect(screen.getByText('Idle (2)')).toBeInTheDocument();
    expect(screen.queryByText(/Retired \(/)).not.toBeInTheDocument();
  });

  it('hides the Composition block when only one breakdown bucket has members', () => {
    render(
      <CivilizationMetrics
        metrics={baseMetrics({ active_members: 5, idle_members: 0, retired_members: 0 })}
      />,
    );
    expect(screen.queryByText('Composition')).not.toBeInTheDocument();
  });

  it('hides the Composition block when no breakdown bucket has members', () => {
    render(
      <CivilizationMetrics
        metrics={baseMetrics({ active_members: 0, idle_members: 0, retired_members: 0 })}
      />,
    );
    expect(screen.queryByText('Composition')).not.toBeInTheDocument();
  });

  it('hides the Spawn Rate chart when spawnHistory is not provided', () => {
    render(<CivilizationMetrics metrics={baseMetrics()} />);
    expect(screen.queryByText('Spawn Rate')).not.toBeInTheDocument();
  });

  it('hides the Spawn Rate chart when spawnHistory has a single entry', () => {
    render(
      <CivilizationMetrics metrics={baseMetrics()} spawnHistory={[{ ts: '2024-01-01', spawns: 2 }]} />,
    );
    expect(screen.queryByText('Spawn Rate')).not.toBeInTheDocument();
  });

  it('renders the Spawn Rate chart when spawnHistory has more than one entry', () => {
    render(
      <CivilizationMetrics
        metrics={baseMetrics()}
        spawnHistory={[
          { ts: '2024-01-01', spawns: 2 },
          { ts: '2024-01-02', spawns: 5 },
        ]}
      />,
    );
    expect(screen.getByText('Spawn Rate')).toBeInTheDocument();
    expect(screen.getByTestId('bar-chart')).toBeInTheDocument();
  });

  it('renders the reputation range labels', () => {
    render(<CivilizationMetrics metrics={baseMetrics({ min_reputation: 0.1, avg_reputation: 0.5, max_reputation: 0.95 })} />);
    expect(screen.getByText('Min 10%')).toBeInTheDocument();
    expect(screen.getByText('Avg 50%')).toBeInTheDocument();
    expect(screen.getByText('Max 95%')).toBeInTheDocument();
  });
});
