import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AgentProfile } from './AgentProfile';

const AGENT = {
  id: 'a1',
  name: 'Ada Agent',
  role: 'Senior Engineer',
  department: 'Engineering',
  status: 'executing',
  reputation: 0.75,
  success_rate: 0.9,
  quality_score: 0.82,
  avg_cost_per_task: 0.12,
  avg_duration_minutes: 4.5,
  primary_model: 'gpt-oss-20b',
  capabilities: ['coding', 'review'],
  allowed_tools: ['github.read', 'jira.create'],
  current_task: 'Refactoring the planner',
  memory_items: [{ content: 'Prefers small PRs', confidence: 0.9 }],
};

afterEach(() => vi.restoreAllMocks());

describe('AgentProfile', () => {
  test('renders the agent header, live task and performance metrics', () => {
    render(<AgentProfile agent={AGENT} onClose={() => {}} />);
    expect(screen.getByRole('heading', { name: 'Ada Agent' })).toBeInTheDocument();
    expect(screen.getByText('Refactoring the planner')).toBeInTheDocument();
    // success_rate 0.9 → "90%"
    expect(screen.getByText('90%')).toBeInTheDocument();
    // primary model badge
    expect(screen.getByText('gpt-oss-20b')).toBeInTheDocument();
    expect(screen.getByText('coding')).toBeInTheDocument();
  });

  test('the Memory tab reveals stored memory items', async () => {
    render(<AgentProfile agent={AGENT} onClose={() => {}} />);
    await userEvent.click(screen.getByRole('tab', { name: 'Memory' }));
    expect(await screen.findByText('Prefers small PRs')).toBeInTheDocument();
  });

  test('the Tools tab lists the allowed tools', async () => {
    render(<AgentProfile agent={AGENT} onClose={() => {}} />);
    await userEvent.click(screen.getByRole('tab', { name: 'Tools' }));
    expect(await screen.findByText('github.read')).toBeInTheDocument();
    expect(screen.getByText('jira.create')).toBeInTheDocument();
  });

  test('the close button invokes onClose', async () => {
    const onClose = vi.fn();
    render(<AgentProfile agent={AGENT} onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: /Close agent profile/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('renders nothing when there is no agent', () => {
    const { container } = render(<AgentProfile agent={null} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
