import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { LimitsPanel } from './LimitsPanel';

describe('LimitsPanel', () => {
  test('renders the plan-limits table with every plan row', () => {
    render(<LimitsPanel />);
    expect(screen.getByText('Plan Limits Comparison')).toBeInTheDocument();
    // Plan names appear both as plan rows and connector-table column headers,
    // so each is present at least once.
    for (const plan of ['Free', 'Starter', 'Professional', 'Enterprise']) {
      expect(screen.getAllByText(plan).length).toBeGreaterThan(0);
    }
    // Per-row cells that are unique to the plan-limits table.
    expect(screen.getByText('5 steps')).toBeInTheDocument();
    expect(screen.getByText('$0.50/goal')).toBeInTheDocument();
    expect(screen.getByText('$50/goal')).toBeInTheDocument();
  });

  test('renders the per-connector rate-limit table', () => {
    render(<LimitsPanel />);
    expect(screen.getByText('Per-Connector Rate Limits')).toBeInTheDocument();
    expect(screen.getByText('Jira')).toBeInTheDocument();
    expect(screen.getByText('GitHub')).toBeInTheDocument();
    expect(screen.getByText('Slack')).toBeInTheDocument();
    // Enterprise column is "Unlimited" for each connector row.
    expect(screen.getAllByText('Unlimited')).toHaveLength(3);
  });

  test('renders the current-usage bars with formatted value/max', () => {
    render(<LimitsPanel />);
    expect(screen.getByText('Current Usage')).toBeInTheDocument();
    expect(screen.getByText('API Requests (this minute)')).toBeInTheDocument();
    // UsageBar formats numbers with toLocaleString: "45 / 120".
    expect(screen.getByText('45 / 120')).toBeInTheDocument();
    expect(screen.getByText('320MB / 500MB')).toBeInTheDocument();
  });

  test('explains the burst rate-limiting window', () => {
    render(<LimitsPanel />);
    expect(screen.getByText('Burst Rate Limiting')).toBeInTheDocument();
    expect(screen.getByText(/10-second burst window/i)).toBeInTheDocument();
  });
});
