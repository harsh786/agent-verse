/**
 * Tests for MissionsList — virtualized missions list with infinite scroll.
 *
 * The data hooks (useMissions, useOrgTasks) are mocked so the component is
 * driven purely by the return values we set per test, matching the pattern
 * used by ActivityFeed.test.tsx for the sibling useOrgEvents hook.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { MissionsList } from './MissionsList';
import type { OrgMission } from '../types';

vi.mock('../hooks/useOrg', () => ({ useMissions: vi.fn(), useOrgTasks: vi.fn() }));

// jsdom has no real layout, so @tanstack/react-virtual's range calculation
// (based on scroll container clientHeight, always 0 in jsdom) never reports
// any item as visible. Stub it to always render every item — this is a
// virtualization-library double, not a change to MissionsList's own logic.
vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: (opts: { count: number; estimateSize: () => number }) => ({
    getVirtualItems: () =>
      Array.from({ length: opts.count }, (_, index) => ({
        index,
        start: index * opts.estimateSize(),
        size: opts.estimateSize(),
        key: index,
      })),
    getTotalSize: () => opts.count * opts.estimateSize(),
  }),
}));

import { useMissions, useOrgTasks } from '../hooks/useOrg';
const mockUseMissions = useMissions as unknown as ReturnType<typeof vi.fn>;
const mockUseOrgTasks = useOrgTasks as unknown as ReturnType<typeof vi.fn>;

function mission(over: Partial<OrgMission> = {}): OrgMission {
  return {
    id: 'm1',
    tenant_id: 't1',
    org_id: 'o1',
    dept_id: null,
    assigned_team_id: null,
    title: 'Ship the launch',
    objective: 'Get it out the door',
    why: '',
    expected_outcome: '',
    status: 'active',
    priority: 'medium',
    source: 'manual',
    autonomy_level: null,
    budget_usd: null,
    deadline: null,
    tags: [],
    created_by: null,
    outputs: [],
    evidence: [],
    started_at: null,
    completed_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...over,
  } as OrgMission;
}

function baseMissionsReturn(over: Partial<ReturnType<typeof mockUseMissions>> = {}) {
  return {
    data: { pages: [{ data: [], cursor: null, hasMore: false }] },
    isLoading: false,
    isFetchingNextPage: false,
    hasNextPage: false,
    fetchNextPage: vi.fn(),
    error: null,
    ...over,
  };
}

afterEach(() => vi.clearAllMocks());

describe('MissionsList', () => {
  test('shows the loading skeleton', () => {
    mockUseMissions.mockReturnValue(baseMissionsReturn({ isLoading: true }));
    mockUseOrgTasks.mockReturnValue({ data: undefined, isLoading: true });
    const { container } = render(<MissionsList orgId="o1" />);
    expect(screen.getByLabelText('Loading missions')).toBeInTheDocument();
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(3);
  });

  test('shows an error state when the query fails', () => {
    mockUseMissions.mockReturnValue(baseMissionsReturn({ error: new Error('boom') }));
    mockUseOrgTasks.mockReturnValue({ data: undefined, isLoading: false });
    render(<MissionsList orgId="o1" />);
    expect(screen.getByRole('alert')).toHaveTextContent(/Failed to load missions/i);
  });

  test('shows the launch-invitation empty state when there are no missions at all', () => {
    mockUseMissions.mockReturnValue(baseMissionsReturn());
    mockUseOrgTasks.mockReturnValue({ data: undefined, isLoading: false });
    const onCreateClick = vi.fn();
    render(<MissionsList orgId="o1" onCreateClick={onCreateClick} />);
    expect(screen.getByText('Your command center is ready')).toBeInTheDocument();
    expect(screen.getByLabelText('Launch your first mission')).toBeInTheDocument();
  });

  test('shows the filtered empty state (no invitation) when a status filter has no matches', () => {
    mockUseMissions.mockReturnValue(baseMissionsReturn());
    mockUseOrgTasks.mockReturnValue({ data: undefined, isLoading: false });
    render(<MissionsList orgId="o1" statusFilter="failed" />);
    expect(screen.getByText('No missions match this filter.')).toBeInTheDocument();
    expect(screen.queryByText('Your command center is ready')).not.toBeInTheDocument();
  });

  test('renders the mission count, and clicking "New Mission" calls onCreateClick', async () => {
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({ data: { pages: [{ data: [mission()], cursor: null, hasMore: false }] } }),
    );
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    const onCreateClick = vi.fn();
    render(<MissionsList orgId="o1" onCreateClick={onCreateClick} />);
    expect(screen.getByText('1 mission')).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText('Create new mission'));
    expect(onCreateClick).toHaveBeenCalledTimes(1);
  });

  test('pluralizes the mission count for more than one mission', () => {
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({
        data: { pages: [{ data: [mission({ id: 'm1' }), mission({ id: 'm2' })], cursor: null, hasMore: false }] },
      }),
    );
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    render(<MissionsList orgId="o1" />);
    expect(screen.getByText('2 missions')).toBeInTheDocument();
  });

  test('does not render the "New Mission" button when onCreateClick is omitted', () => {
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({ data: { pages: [{ data: [mission()], cursor: null, hasMore: false }] } }),
    );
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    render(<MissionsList orgId="o1" />);
    expect(screen.queryByLabelText('Create new mission')).not.toBeInTheDocument();
  });

  test('filters missions to the given department', () => {
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({
        data: {
          pages: [{
            data: [
              mission({ id: 'm1', dept_id: 'eng' }),
              mission({ id: 'm2', dept_id: 'sales' }),
            ],
            cursor: null,
            hasMore: false,
          }],
        },
      }),
    );
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    render(<MissionsList orgId="o1" deptFilter="eng" />);
    expect(screen.getByText('1 mission')).toBeInTheDocument();
  });

  test('derives per-mission progress from the shared org-wide tasks query', () => {
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({ data: { pages: [{ data: [mission({ id: 'm1' })], cursor: null, hasMore: false }] } }),
    );
    mockUseOrgTasks.mockReturnValue({
      data: {
        data: [
          { id: 't1', mission_id: 'm1', status: 'completed' },
          { id: 't2', mission_id: 'm1', status: 'running' },
        ],
      },
      isLoading: false,
    });
    render(<MissionsList orgId="o1" />);
    expect(screen.getByLabelText('Progress: 50%')).toBeInTheDocument();
  });

  test('passes undefined progress (no progress bar yet) while the shared tasks query is still loading', () => {
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({ data: { pages: [{ data: [mission({ id: 'm1' })], cursor: null, hasMore: false }] } }),
    );
    // tasksLoading true → MissionsList passes progress=undefined to the card;
    // MissionCard isn't given an orgId of its own, so it renders no progress
    // bar at all rather than fetching independently — this is the "no per-card
    // fetch" guarantee this component exists to provide.
    mockUseOrgTasks.mockReturnValue({ data: undefined, isLoading: true });
    render(<MissionsList orgId="o1" />);
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  test('clicking a mission card invokes onMissionClick with the full mission object', async () => {
    const m = mission({ id: 'm7', title: 'Deploy the new pricing page' });
    mockUseMissions.mockReturnValue(baseMissionsReturn({ data: { pages: [{ data: [m], cursor: null, hasMore: false }] } }));
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    const onMissionClick = vi.fn();
    render(<MissionsList orgId="o1" onMissionClick={onMissionClick} />);

    await userEvent.click(screen.getByText('Deploy the new pricing page'));
    expect(onMissionClick).toHaveBeenCalledWith(m);
  });

  test('shows the "loading more" spinner while fetching the next page', () => {
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({
        data: { pages: [{ data: [mission()], cursor: 'c2', hasMore: true }] },
        isFetchingNextPage: true,
        hasNextPage: true,
      }),
    );
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    render(<MissionsList orgId="o1" />);
    expect(screen.getByLabelText('Loading more missions')).toBeInTheDocument();
  });

  test('fetches the next page on scroll near the bottom when more pages remain', () => {
    const fetchNextPage = vi.fn();
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({
        data: { pages: [{ data: [mission()], cursor: 'c2', hasMore: true }] },
        hasNextPage: true,
        fetchNextPage,
      }),
    );
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    const { container } = render(<MissionsList orgId="o1" />);
    const scrollEl = container.querySelector('[aria-label="1 missions"]') as HTMLElement;
    fireEvent.scroll(scrollEl);
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });

  test('does not fetch the next page on scroll when there is no next page', () => {
    const fetchNextPage = vi.fn();
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({
        data: { pages: [{ data: [mission()], cursor: null, hasMore: false }] },
        hasNextPage: false,
        fetchNextPage,
      }),
    );
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    const { container } = render(<MissionsList orgId="o1" />);
    const scrollEl = container.querySelector('[aria-label="1 missions"]') as HTMLElement;
    fireEvent.scroll(scrollEl);
    expect(fetchNextPage).not.toHaveBeenCalled();
  });

  test('does not fetch the next page on scroll while already fetching one', () => {
    const fetchNextPage = vi.fn();
    mockUseMissions.mockReturnValue(
      baseMissionsReturn({
        data: { pages: [{ data: [mission()], cursor: 'c2', hasMore: true }] },
        hasNextPage: true,
        isFetchingNextPage: true,
        fetchNextPage,
      }),
    );
    mockUseOrgTasks.mockReturnValue({ data: { data: [] }, isLoading: false });
    const { container } = render(<MissionsList orgId="o1" />);
    const scrollEl = container.querySelector('[aria-label="1 missions"]') as HTMLElement;
    fireEvent.scroll(scrollEl);
    expect(fetchNextPage).not.toHaveBeenCalled();
  });
});
