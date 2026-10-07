import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PromoteToGoldenDataset } from './PromoteToGoldenDataset';

const listSuites = vi.fn();
const promoteGoal = vi.fn();
vi.mock('@/lib/api/client', () => ({
  evalSuitesApi: {
    listSuites: () => listSuites(),
    promoteGoal: (...args: unknown[]) => promoteGoal(...args),
  },
}));

function renderIt(status = 'complete', dryRun = false) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PromoteToGoldenDataset goalId="g-done" status={status} dryRun={dryRun} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  listSuites.mockReset();
  promoteGoal.mockReset();
  listSuites.mockResolvedValue([
    { suite_id: 's-ops', name: 'Ops regressions', task_count: 3, created_at: '', dataset_version: 4 },
  ]);
});

describe('PromoteToGoldenDataset', () => {
  it('is offered only for a completed, executed goal', () => {
    const { unmount } = renderIt('failed');
    expect(screen.queryByRole('button', { name: /promote to golden dataset/i })).toBeNull();
    unmount();
    renderIt('complete', true);
    expect(screen.queryByRole('button', { name: /promote to golden dataset/i })).toBeNull();
  });

  it('promotes the goal into the chosen suite and shows the new dataset version', async () => {
    promoteGoal.mockResolvedValue({
      suite_id: 's-ops', goal_id: 'g-done', task_id: 'goal-g-done', dataset_version: 5,
      task: {},
    });
    const user = userEvent.setup();
    renderIt();
    await user.click(screen.getByRole('button', { name: /promote to golden dataset/i }));
    const dialog = await screen.findByRole('dialog', { name: /promote to golden dataset/i });
    expect(dialog).toBeInTheDocument();

    const promoteBtn = screen.getByRole('button', { name: /^promote$/i });
    expect(promoteBtn).toBeDisabled(); // no suite chosen yet
    await user.selectOptions(await screen.findByRole('combobox'), 's-ops');
    await user.type(screen.getByPlaceholderText('incident, ingest'), 'incident, ingest');
    await user.click(screen.getByLabelText(/require the sources it cited/i)); // opt out
    await user.click(promoteBtn);

    await waitFor(() => expect(promoteGoal).toHaveBeenCalledTimes(1));
    expect(promoteGoal).toHaveBeenCalledWith('s-ops', 'g-done', {
      tags: ['incident', 'ingest'],
      context: undefined,
      include_tools: true,
      include_citations: false,
    });
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent('Ops regressions');
    expect(status).toHaveTextContent('goal-g-done');
    expect(status).toHaveTextContent('dataset version 5');
  });

  it('shows the API refusal (e.g. already promoted)', async () => {
    promoteGoal.mockRejectedValue(new Error('Goal g-done is already a golden task of this suite'));
    const user = userEvent.setup();
    renderIt();
    await user.click(screen.getByRole('button', { name: /promote to golden dataset/i }));
    await user.selectOptions(await screen.findByRole('combobox'), 's-ops');
    await user.click(screen.getByRole('button', { name: /^promote$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already a golden task');
  });
});
