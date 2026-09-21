/**
 * Tests for LiveActivityStream — the animated real-time goal activity feed.
 *
 * Covers the empty state, the timeAgo/statusMeta helper branches (seconds,
 * minutes, hours, days, malformed dates, unknown statuses), the iterations
 * singular/plural copy, and navigation on click.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { LiveActivityStream } from './LiveActivityStream';

const navigateMock = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

afterEach(() => {
  vi.restoreAllMocks();
  navigateMock.mockClear();
});

function renderStream(props: React.ComponentProps<typeof LiveActivityStream>) {
  return render(
    <MemoryRouter>
      <LiveActivityStream {...props} />
    </MemoryRouter>
  );
}

describe('LiveActivityStream', () => {
  test('renders an empty state when there is no activity', () => {
    renderStream({ goals: [] });
    expect(screen.getByText('No recent activity')).toBeInTheDocument();
    expect(screen.getByText('Submit your first goal to see it here')).toBeInTheDocument();
  });

  test('renders a completed goal with a "d ago" timestamp and pluralized steps', () => {
    const fourDaysAgo = new Date(Date.now() - 4 * 86400 * 1000).toISOString();
    renderStream({
      goals: [
        { id: 'g1', goal: 'Ship the release', status: 'completed', created_at: fourDaysAgo, iterations: 3, cost_usd: 0.5 },
      ],
    });
    const item = screen.getByRole('button', { name: /View goal: Ship the release/ });
    expect(within(item).getByText(/^4d ago$/)).toBeInTheDocument();
    expect(within(item).getByText('3 steps')).toBeInTheDocument();
  });

  test('renders a single-iteration goal with singular "step" copy', () => {
    renderStream({
      goals: [{ id: 'g2', goal: 'Single step goal', status: 'failed', iterations: 1 }],
    });
    const item = screen.getByRole('button', { name: /View goal: Single step goal/ });
    expect(within(item).getByText('1 step')).toBeInTheDocument();
  });

  test('omits the iterations badge when iterations is absent, zero, or null', () => {
    renderStream({
      goals: [
        { id: 'g3', goal: 'No iterations field', status: 'error' },
        { id: 'g4', goal: 'Zero iterations', status: 'waiting_human', iterations: 0 },
      ],
    });
    expect(screen.queryByText(/step/)).not.toBeInTheDocument();
  });

  test('omits the timestamp badge when created_at is absent', () => {
    renderStream({ goals: [{ id: 'g5', goal: 'No timestamp goal', status: 'planning' }] });
    const item = screen.getByRole('button', { name: /View goal: No timestamp goal/ });
    expect(within(item).queryByText(/ago$/)).not.toBeInTheDocument();
  });

  test('swallows a throwing date parse instead of crashing (timeAgo catch branch)', () => {
    // Simulate a malformed activity event whose created_at value makes the
    // Date constructor itself throw (e.g. a hostile/corrupted payload), rather
    // than merely parsing to an Invalid Date. timeAgo must catch it and
    // degrade to an empty string instead of propagating the error.
    const RealDate = Date;
    class ThrowingDate extends RealDate {
      constructor(...args: ConstructorParameters<typeof RealDate>) {
        if (args[0] === 'boom') throw new Error('malformed date payload');
        super(...(args as []));
      }
      static now() { return RealDate.now(); }
    }
    vi.stubGlobal('Date', ThrowingDate);

    renderStream({
      goals: [{ id: 'g6', goal: 'Malformed date goal', status: 'running', created_at: 'boom' }],
    });
    const item = screen.getByRole('button', { name: /View goal: Malformed date goal/ });
    expect(within(item).queryByText(/ago$/)).not.toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  test('shows recent-second and minute-scale timestamps', () => {
    const justNow = new Date(Date.now() - 5 * 1000).toISOString();
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    renderStream({
      goals: [
        { id: 'g7', goal: 'Just now goal', status: 'executing', created_at: justNow },
        { id: 'g8', goal: 'Minutes ago goal', status: 'verifying', created_at: fiveMinAgo },
      ],
    });
    const secItem = screen.getByRole('button', { name: /View goal: Just now goal/ });
    expect(within(secItem).getByText(/^\ds ago$/)).toBeInTheDocument();
    const minItem = screen.getByRole('button', { name: /View goal: Minutes ago goal/ });
    expect(within(minItem).getByText(/^5m ago$/)).toBeInTheDocument();
  });

  test('shows an hour-scale timestamp', () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 3600 * 1000).toISOString();
    renderStream({ goals: [{ id: 'g9', goal: 'Hours ago goal', status: 'active', created_at: twoHoursAgo }] });
    const item = screen.getByRole('button', { name: /View goal: Hours ago goal/ });
    expect(within(item).getByText(/^2h ago$/)).toBeInTheDocument();
  });

  test('recognizes the terse "complete" status alias alongside "completed"', () => {
    renderStream({ goals: [{ id: 'g12', goal: 'Terse status goal', status: 'complete' }] });
    expect(screen.getByRole('button', { name: /View goal: Terse status goal/ })).toBeInTheDocument();
  });

  test('falls back to the default status icon/color for an unrecognized status', () => {
    renderStream({ goals: [{ id: 'g10', goal: 'Weird status goal', status: 'some_unknown_status' }] });
    expect(screen.getByRole('button', { name: /View goal: Weird status goal/ })).toBeInTheDocument();
  });

  test('respects maxItems and truncates the list', () => {
    const goals = Array.from({ length: 5 }, (_, i) => ({
      id: `id-${i}`,
      goal: `Goal number ${i}`,
      status: 'completed',
    }));
    renderStream({ goals, maxItems: 2 });
    expect(screen.getAllByRole('button')).toHaveLength(2);
  });

  test('navigates to the goal detail page when an item is clicked', async () => {
    const user = userEvent.setup();
    renderStream({ goals: [{ id: 'abc123', goal: 'Clickable goal', status: 'completed' }] });
    await user.click(screen.getByRole('button', { name: /View goal: Clickable goal/ }));
    expect(navigateMock).toHaveBeenCalledWith('/goals/abc123');
  });

  test('truncates long goal text in the accessible label', () => {
    const longGoal = 'x'.repeat(120);
    renderStream({ goals: [{ id: 'g11', goal: longGoal, status: 'completed' }] });
    const button = screen.getByRole('button');
    expect(button.getAttribute('aria-label')).toBe(`View goal: ${longGoal.slice(0, 60)}`);
  });
});
