import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ActivityFeed } from './ActivityFeed';

// ActivityFeed reads its data through useOrgEvents; mock the hook module so the
// component is driven purely by the return value we set per test.
vi.mock('../hooks/useOrg', () => ({ useOrgEvents: vi.fn() }));
import { useOrgEvents } from '../hooks/useOrg';
const mockUseOrgEvents = useOrgEvents as unknown as ReturnType<typeof vi.fn>;

interface FeedEvent {
  id: string;
  event_type: string;
  title: string;
  description: string;
  severity: string;
  created_at: string;
}

const EVENTS: FeedEvent[] = [
  {
    id: 'e1',
    event_type: 'mission_completed',
    title: 'Mission completed',
    description: 'Quarterly report shipped',
    severity: 'success',
    created_at: '2026-09-16T10:15:00.000Z',
  },
  {
    id: 'e2',
    event_type: 'agent_spawned',
    title: '', // falls back to a humanised event_type
    description: '',
    severity: 'info',
    created_at: '2026-09-16T09:00:00.000Z',
  },
];

afterEach(() => {
  vi.clearAllMocks();
});

describe('ActivityFeed', () => {
  test('renders the skeleton (no empty state) while loading', () => {
    mockUseOrgEvents.mockReturnValue({ data: undefined, isLoading: true });
    const { container } = render(<ActivityFeed orgId="org-1" />);
    expect(screen.getByText('Activity')).toBeInTheDocument();
    expect(container.querySelector('.animate-pulse')).not.toBeNull();
    expect(screen.queryByText(/No activity yet/i)).not.toBeInTheDocument();
  });

  test('renders the empty state when there are no events', () => {
    mockUseOrgEvents.mockReturnValue({ data: [], isLoading: false });
    render(<ActivityFeed orgId="org-1" />);
    expect(screen.getByText(/No activity yet/i)).toBeInTheDocument();
    expect(screen.getByText(/Events will appear here as missions run/i)).toBeInTheDocument();
  });

  test('renders each event title, falling back to a humanised event_type', () => {
    mockUseOrgEvents.mockReturnValue({ data: EVENTS, isLoading: false });
    render(<ActivityFeed orgId="org-1" />);
    expect(screen.getByText('Mission completed')).toBeInTheDocument();
    // e2 has an empty title, so its underscored event_type is shown with spaces.
    expect(screen.getByText('agent spawned')).toBeInTheDocument();
  });

  test('renders an event description when present', () => {
    mockUseOrgEvents.mockReturnValue({ data: EVENTS, isLoading: false });
    render(<ActivityFeed orgId="org-1" />);
    expect(screen.getByText('Quarterly report shipped')).toBeInTheDocument();
  });

  test('renders a machine-readable timestamp for each event', () => {
    mockUseOrgEvents.mockReturnValue({ data: [EVENTS[0]], isLoading: false });
    const { container } = render(<ActivityFeed orgId="org-1" />);
    const time = container.querySelector('time');
    expect(time).not.toBeNull();
    expect(time!.getAttribute('datetime')).toBe('2026-09-16T10:15:00.000Z');
  });

  test('caps the rendered list at maxItems', () => {
    const many: FeedEvent[] = [
      { ...EVENTS[0], id: 'a' },
      { ...EVENTS[0], id: 'b' },
      { ...EVENTS[0], id: 'c' },
    ];
    mockUseOrgEvents.mockReturnValue({ data: many, isLoading: false });
    const { container } = render(<ActivityFeed orgId="org-1" maxItems={2} />);
    expect(container.querySelectorAll('li')).toHaveLength(2);
  });
});
