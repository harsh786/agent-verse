/** Phase 7 — panel renders cards from the canonical chat-event vocabulary. */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AgenticExecutionPanel } from './AgenticExecutionPanel';

const canonical = [
  { type: 'tool_call', tool: 'jira.search' },
  { type: 'step_started', description: 'Search Jira' },
  { type: 'knowledge_retrieved' },
  { type: 'guardrail_blocked', reason: 'prod delete blocked' },
  { type: 'goal_complete' },
] as unknown as never;

describe('AgenticExecutionPanel canonical events', () => {
  it('renders tool/step/knowledge/guardrail/complete cards', () => {
    render(<AgenticExecutionPanel events={canonical} isActive />);
    expect(screen.getByText('jira.search')).toBeDefined();
    expect(screen.getByText('Search Jira')).toBeDefined();
    expect(screen.getByText('Knowledge retrieved')).toBeDefined();
    expect(screen.getByText(/Blocked:/)).toBeDefined();
    expect(screen.getByText('Goal complete')).toBeDefined();
  });
});
