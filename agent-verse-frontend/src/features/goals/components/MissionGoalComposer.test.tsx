import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, test, vi } from 'vitest';
import { MissionGoalComposer } from './MissionGoalComposer';

const { submit } = vi.hoisted(() => ({
  submit: vi.fn().mockResolvedValue({ goal_id: 'goal-1' }),
}));

vi.mock('@/lib/api/client', () => ({
  agentsApi: { list: vi.fn().mockResolvedValue([]) },
  goalsApi: { submit },
  apiFetch: vi.fn((path: string) => Promise.resolve(path === '/strategies' ? {
    strategies: [{ strategy_id: 'react', derived_state: 'implemented', ready: true, certified: false }],
  } : { models: [] })),
}));
vi.mock('@/features/templates/components/TemplatePickerModal', () => ({ TemplatePickerModal: () => null }));
vi.mock('./CostEstimateWidget', () => ({ CostEstimateWidget: () => null }));
vi.mock('@/components/voice/VoiceGoalInput', () => ({ VoiceGoalInput: () => null }));

function view() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><MissionGoalComposer /></MemoryRouter></QueryClientProvider>);
}

describe('MissionGoalComposer strategy controls', () => {
  test('keeps override fields absent by default', async () => {
    view();
    fireEvent.change(screen.getByLabelText('Goal text'), { target: { value: 'Run a safe goal' } });
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));
    await waitFor(() => expect(submit).toHaveBeenCalled());
    const lastCall = submit.mock.calls[submit.mock.calls.length - 1]?.[0];
    expect(lastCall).not.toHaveProperty('strategy_override');
    expect(lastCall).not.toHaveProperty('pattern_limits');
  });

  test('submits selected strategy and bounded limit values', async () => {
    view();
    fireEvent.click(screen.getByRole('button', { name: 'Options' }));
    await screen.findByRole('option', { name: /react.*implemented.*ready.*uncertified/i });
    fireEvent.change(screen.getByLabelText('Runtime'), { target: { value: 'react' } });
    fireEvent.change(screen.getByLabelText('Calls'), { target: { value: '4' } });
    fireEvent.change(screen.getByLabelText('Goal text'), { target: { value: 'Run a bounded goal' } });
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(expect.objectContaining({ strategy_override: 'react', pattern_limits: { calls: 4 } })));
  });
});
