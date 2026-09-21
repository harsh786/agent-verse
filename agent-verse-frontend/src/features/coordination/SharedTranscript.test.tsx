import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import { SharedTranscript } from './SharedTranscript';
import type { CoordinationMessage } from './types';

describe('SharedTranscript', () => {
  test('renders empty state when there are no messages', () => {
    render(<SharedTranscript messages={[]} />);
    expect(screen.getByText('No messages yet.')).toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  test('renders a fully-populated message with all optional fields', () => {
    const message: CoordinationMessage = {
      message_id: 'm-1',
      sequence: 5,
      sender_agent_id: 'agent-a',
      recipient_agent_ids: ['agent-b', 'agent-c'],
      safe_content: 'Hello world',
      classification: 'public',
      trust_label: 'trusted',
      citation_ids: ['cite-1', 'cite-2'],
      compacts_from_sequence: 1,
      compacts_to_sequence: 4,
    };
    render(<SharedTranscript messages={[message]} />);

    expect(screen.getByText('#5')).toBeInTheDocument();
    expect(screen.getByText('agent-a')).toBeInTheDocument();
    expect(screen.getByText('→ agent-b, agent-c')).toBeInTheDocument();
    expect(screen.getByText('public')).toBeInTheDocument();
    expect(screen.getByText('trusted')).toBeInTheDocument();
    expect(screen.getByText('Hello world')).toBeInTheDocument();
    expect(screen.getByText('Citations: cite-1, cite-2')).toBeInTheDocument();
    expect(screen.getByText('Compacts #1–#4')).toBeInTheDocument();
  });

  test('falls back to defaults when optional fields are missing', () => {
    const message: CoordinationMessage = { sequence: 1 };
    render(<SharedTranscript messages={[message]} />);

    expect(screen.getByText('system')).toBeInTheDocument();
    expect(screen.getByText('→ all')).toBeInTheDocument();
    expect(screen.getByText('internal')).toBeInTheDocument();
    expect(screen.getByText('unlabelled')).toBeInTheDocument();
    expect(screen.getByText('Event recorded')).toBeInTheDocument();
    expect(screen.queryByText(/Citations:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Compacts/)).not.toBeInTheDocument();
  });

  test('prefers artifact_reference over the "Event recorded" fallback when safe_content is absent', () => {
    const message: CoordinationMessage = { sequence: 2, artifact_reference: 'artifact://blob/42' };
    render(<SharedTranscript messages={[message]} />);
    expect(screen.getByText('artifact://blob/42')).toBeInTheDocument();
    expect(screen.queryByText('Event recorded')).not.toBeInTheDocument();
  });

  test('renders an empty citation_ids array without a Citations line', () => {
    const message: CoordinationMessage = { sequence: 3, citation_ids: [] };
    render(<SharedTranscript messages={[message]} />);
    expect(screen.queryByText(/Citations:/)).not.toBeInTheDocument();
  });

  test('falls back to index-based keys when message_id and sequence are both absent, rendering multiple messages', () => {
    const messages = [
      { sequence: undefined, sender_agent_id: 'a1' } as unknown as CoordinationMessage,
      { sequence: undefined, sender_agent_id: 'a2' } as unknown as CoordinationMessage,
    ];
    render(<SharedTranscript messages={messages} />);
    expect(screen.getByText('a1')).toBeInTheDocument();
    expect(screen.getByText('a2')).toBeInTheDocument();
  });

  test('renders compacts_from_sequence of 0 (falsy but not nullish)', () => {
    const message: CoordinationMessage = { sequence: 6, compacts_from_sequence: 0, compacts_to_sequence: 2 };
    render(<SharedTranscript messages={[message]} />);
    expect(screen.getByText('Compacts #0–#2')).toBeInTheDocument();
  });
});
