import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TriggerCreateModal } from './TriggerCreateModal';

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/agents'))
      return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/goals'))
      return new Response(JSON.stringify({ goals: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/triggers') && method === 'POST')
      return new Response(JSON.stringify({ schedule_id: 'new-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderModal(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TriggerCreateModal onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TriggerCreateModal', () => {
  test('opens on the family step listing creatable families', () => {
    mockFetch();
    renderModal();
    expect(screen.getByRole('heading', { name: /choose a trigger family/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Time & Schedule/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Conversational/i })).toBeInTheDocument();
  });

  test('choosing a family advances to the type step for that family', async () => {
    mockFetch();
    renderModal();
    await userEvent.click(screen.getByRole('button', { name: /Time & Schedule/i }));
    expect(screen.getByRole('heading', { name: 'Time & Schedule' })).toBeInTheDocument();
    // Time family exposes the cron type.
    expect(screen.getByRole('button', { name: 'cron' })).toBeInTheDocument();
  });

  test('choosing a type advances to the config step with the goal-binding controls', async () => {
    mockFetch();
    renderModal();
    await userEvent.click(screen.getByRole('button', { name: /Time & Schedule/i }));
    await userEvent.click(screen.getByRole('button', { name: 'cron' }));
    expect(screen.getByText(/Configure/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Bind to an existing goal/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Run as agent/i)).toBeInTheDocument();
  });

  test('Create Trigger is disabled until a goal, template, or agent is provided; then it POSTs', async () => {
    const spy = mockFetch();
    renderModal();
    await userEvent.click(screen.getByRole('button', { name: /Time & Schedule/i }));
    await userEvent.click(screen.getByRole('button', { name: 'cron' }));

    const createBtn = screen.getByRole('button', { name: /create trigger/i });
    expect(createBtn).toBeDisabled();

    await userEvent.type(
      screen.getByPlaceholderText(/Describe the goal to create when this trigger fires/i),
      'Run nightly backup',
    );
    expect(createBtn).toBeEnabled();

    await userEvent.click(createBtn);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => String(u).includes('/triggers') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('the close button invokes onClose', async () => {
    mockFetch();
    const onClose = renderModal();
    await userEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
