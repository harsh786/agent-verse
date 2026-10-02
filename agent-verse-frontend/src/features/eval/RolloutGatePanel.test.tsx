/**
 * RolloutGatePanel (MEM-52): shows which run vouches for the agent (dataset
 * version, config match) and starts a gate run against THIS agent.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { RolloutGatePanel } from './RolloutGatePanel';

function renderPanel(report: Parameters<typeof RolloutGatePanel>[0]['report']) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RolloutGatePanel agentId="agent-1" report={report} />
    </QueryClientProvider>,
  );
}

describe('RolloutGatePanel', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  });
  afterEach(() => vi.restoreAllMocks());

  test('names the vouching run, its dataset version and whether it ran the current config', () => {
    renderPanel({
      gate_passed: false, eval_suite_id: 's1', pass_rate: 1, run_count: 1,
      run_id: 'run-9', total_tasks: 6, dataset_version: 2, current_dataset_version: 3,
      agent_config_hash: 'h-new', run_agent_config_hash: 'h-old', min_suite_size: 5,
      reason: 'The golden dataset changed',
    });
    const line = screen.getByTestId('rollout-vouching-run');
    expect(line).toHaveTextContent('run-9');
    expect(line).toHaveTextContent('dataset v2 (current v3)');
    expect(line).toHaveTextContent('older agent config');
    expect(screen.getByText(/Gate blocked/)).toBeInTheDocument();
  });

  test('"Run gate suite" runs the attached suite against this agent', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ run_id: 'r1' }), { status: 202, headers: { 'Content-Type': 'application/json' } }),
    );
    renderPanel({ gate_passed: false, eval_suite_id: 's1', run_count: 0 });
    await userEvent.click(screen.getByRole('button', { name: 'Run gate suite' }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toMatch(/\/intelligence\/eval-suites\/s1\/run$/);
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ agent_id: 'agent-1' });
  });

  test('no suite attached: no gate-run button', () => {
    renderPanel({ gate_passed: false, eval_suite_id: null });
    expect(screen.queryByRole('button', { name: 'Run gate suite' })).not.toBeInTheDocument();
    expect(screen.getByTestId('rollout-suite')).toHaveTextContent('none attached');
  });
});
