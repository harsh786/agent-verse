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

  test('falls back to defaults for a minimal agent with no optional fields', () => {
    render(<AgentProfile agent={{ id: 'a2', name: 'Bare Agent' }} onClose={() => {}} />);
    // No status → defaults to "Idle".
    expect(screen.getByText('Idle')).toBeInTheDocument();
    // No current_task banner.
    expect(screen.queryByText(/Refactoring/i)).not.toBeInTheDocument();
    // Metrics fall back to zeroed values.
    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.getByText('0.00')).toBeInTheDocument();
    expect(screen.getByText('$0.00')).toBeInTheDocument();
    expect(screen.getByText('0.0 min')).toBeInTheDocument();
    // No primary_model → no "Current Session" card.
    expect(screen.queryByText('Current Session')).not.toBeInTheDocument();
    // No capabilities → no Capabilities card.
    expect(screen.queryByText('Capabilities')).not.toBeInTheDocument();
  });

  test('falls back to the idle status config for an unrecognized status', () => {
    render(<AgentProfile agent={{ id: 'a3', name: 'Odd Agent', status: 'some-unknown-status' }} onClose={() => {}} />);
    expect(screen.getByText('Idle')).toBeInTheDocument();
  });

  test('the Memory tab shows an empty state when there are no memory items', async () => {
    render(<AgentProfile agent={{ id: 'a4', name: 'Empty Memory Agent' }} onClose={() => {}} />);
    await userEvent.click(screen.getByRole('tab', { name: 'Memory' }));
    expect(await screen.findByText('No memory items yet.')).toBeInTheDocument();
  });

  test('the Tools tab shows an empty state when there are no allowed tools', async () => {
    render(<AgentProfile agent={{ id: 'a5', name: 'No Tools Agent' }} onClose={() => {}} />);
    await userEvent.click(screen.getByRole('tab', { name: 'Tools' }));
    expect(await screen.findByText('No tools configured.')).toBeInTheDocument();
  });

  test('renders token usage and cost when a primary model is active', () => {
    render(
      <AgentProfile
        agent={{
          id: 'a6', name: 'Model Agent', primary_model: 'claude-sonnet',
          tokens_in: 1200, tokens_out: 340, cost_usd: 0.045,
        }}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText('Current Session')).toBeInTheDocument();
    expect(screen.getByText(/1,200 in \/ 340 out/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.045/)).toBeInTheDocument();
  });

  test('omits the token/cost line when a model is set but no tokens were used', () => {
    render(<AgentProfile agent={{ id: 'a7', name: 'Idle Model Agent', primary_model: 'claude-sonnet' }} onClose={() => {}} />);
    expect(screen.getByText('Current Session')).toBeInTheDocument();
    expect(screen.queryByText(/in \/.*out/)).not.toBeInTheDocument();
  });

  test('renders role and department separator when both are present', () => {
    render(<AgentProfile agent={AGENT} onClose={() => {}} />);
    expect(screen.getByText('Senior Engineer')).toBeInTheDocument();
    expect(screen.getByText('Engineering')).toBeInTheDocument();
  });
});
