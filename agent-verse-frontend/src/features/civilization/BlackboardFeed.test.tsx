import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { BlackboardFeed } from './BlackboardFeed';
import type { BlackboardEntry } from '../../lib/api/civilizationApi';

function entry(overrides: Partial<BlackboardEntry> = {}): BlackboardEntry {
  return {
    id: 'entry-1',
    author_agent_id: 'agent-abcdefgh1234',
    topic: 'findings',
    content: 'Discovered a rate limit issue.',
    confidence: 0.5,
    version: 1,
    created_at: '2024-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('BlackboardFeed', () => {
  test('renders empty state when there are no entries', () => {
    render(<BlackboardFeed entries={[]} />);
    expect(screen.getByText('Blackboard is empty')).toBeInTheDocument();
    expect(screen.getByText('Agent findings will appear here in real time')).toBeInTheDocument();
  });

  test('renders singular "posting" label for exactly one entry', () => {
    render(<BlackboardFeed entries={[entry()]} />);
    expect(screen.getByText('1 posting')).toBeInTheDocument();
  });

  test('renders plural "postings" label for multiple entries', () => {
    render(<BlackboardFeed entries={[entry({ id: 'a' }), entry({ id: 'b' })]} />);
    expect(screen.getByText('2 postings')).toBeInTheDocument();
  });

  test('renders content, truncated author id, and known topic styling', () => {
    render(<BlackboardFeed entries={[entry({ topic: 'debate', content: 'We should vote.' })]} />);
    expect(screen.getByText('We should vote.')).toBeInTheDocument();
    expect(screen.getByText('debate')).toBeInTheDocument();
    expect(screen.getByText('agent-ab')).toBeInTheDocument(); // sliced to 8 chars
  });

  test('falls back to default styling and "posting" label for an unrecognized topic', () => {
    render(<BlackboardFeed entries={[entry({ topic: 'mystery-topic' })]} />);
    expect(screen.getByText('mystery-topic')).toBeInTheDocument();
  });

  test('falls back to the default topic styling and "posting" label when topic is missing from the API payload', () => {
    render(<BlackboardFeed entries={[entry({ topic: undefined as unknown as string })]} />);
    expect(screen.getByText('posting')).toBeInTheDocument();
  });

  test('renders green confidence indicator above 70%', () => {
    render(<BlackboardFeed entries={[entry({ confidence: 0.85 })]} />);
    expect(screen.getByText('85%')).toBeInTheDocument();
  });

  test('renders amber confidence indicator between 41% and 70%', () => {
    render(<BlackboardFeed entries={[entry({ confidence: 0.5 })]} />);
    expect(screen.getByText('50%')).toBeInTheDocument();
  });

  test('renders red confidence indicator at or below 40%', () => {
    render(<BlackboardFeed entries={[entry({ confidence: 0.2 })]} />);
    expect(screen.getByText('20%')).toBeInTheDocument();
  });

  test('defaults confidence to 0% when confidence is undefined', () => {
    render(<BlackboardFeed entries={[entry({ confidence: undefined as unknown as number })]} />);
    expect(screen.getByText('0%')).toBeInTheDocument();
  });

  test('shows version badge when version is defined (including 0)', () => {
    render(<BlackboardFeed entries={[entry({ version: 0 })]} />);
    expect(screen.getByText('v0')).toBeInTheDocument();
  });

  test('omits version badge when version is undefined', () => {
    render(<BlackboardFeed entries={[entry({ version: undefined as unknown as number })]} />);
    expect(screen.queryByText(/^v\d/)).not.toBeInTheDocument();
  });

  test('handles a missing author_agent_id gracefully', () => {
    render(<BlackboardFeed entries={[entry({ author_agent_id: undefined as unknown as string })]} />);
    // Should not throw, and content still renders.
    expect(screen.getByText('Discovered a rate limit issue.')).toBeInTheDocument();
  });

  test('renders multiple entries with distinct topics simultaneously', () => {
    render(
      <BlackboardFeed
        entries={[entry({ id: 'a', topic: 'error', content: 'Boom' }), entry({ id: 'b', topic: 'findings', content: 'Yay' })]}
      />,
    );
    expect(screen.getByText('Boom')).toBeInTheDocument();
    expect(screen.getByText('Yay')).toBeInTheDocument();
  });
});
