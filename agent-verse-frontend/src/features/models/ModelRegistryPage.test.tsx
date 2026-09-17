import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ModelRegistryPage } from './ModelRegistryPage';

const REGISTRY = {
  total: 2,
  capabilities: [
    {
      capability: 'text_generation',
      selected_model_id: 'cheap-llm',
      models: [
        { provider: 'nvidia', model_id: 'pricey-llm', cost_per_1k_input: 0.02, supports_tools: true, supports_vision: false },
        { provider: 'custom', model_id: 'cheap-llm', cost_per_1k_input: 0, supports_tools: true, supports_vision: false },
      ],
    },
  ],
};

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/models/configured/reseed'))
      return new Response(JSON.stringify({ status: 'ok', configured_models: 3 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/models/configured') && method === 'POST')
      return new Response(JSON.stringify({ status: 'ok', model_id: 'gpt-oss-20b' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/models/configured') && method === 'DELETE')
      return new Response(JSON.stringify({ status: 'deleted' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/models/configured'))
      return new Response(JSON.stringify(REGISTRY), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ModelRegistryPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ModelRegistryPage', () => {
  test('renders capability groups and marks the cheapest model as in use', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /Model Registry/i })).toBeInTheDocument();
    expect(await screen.findByText('cheap-llm')).toBeInTheDocument();
    expect(screen.getByText('pricey-llm')).toBeInTheDocument();
    // The selected (cheapest) model shows the IN USE badge.
    expect(screen.getByText(/IN USE \(cheapest\)/i)).toBeInTheDocument();
  });

  test('admin-only actions are disabled until the platform admin key is entered', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    const addBtn = screen.getByRole('button', { name: /Add Model/i });
    const reseedBtn = screen.getByRole('button', { name: /Reseed from config/i });
    expect(addBtn).toBeDisabled();
    expect(reseedBtn).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    expect(addBtn).toBeEnabled();
    expect(reseedBtn).toBeEnabled();
  });

  test('add-model modal validates required fields then POSTs with the admin header', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));

    // Save with empty model id → inline validation error, no POST yet.
    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/Model ID and at least one capability/i);
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);

    // Fill the model id and save → POST fires with the admin key header.
    await userEvent.type(screen.getByPlaceholderText(/openai\/gpt-oss-20b/i), 'gpt-oss-20b');
    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) =>
          String(u).includes('/models/configured') &&
          (i as RequestInit)?.method === 'POST' &&
          ((i as RequestInit)?.headers as Record<string, string>)?.['X-Admin-Key'] === 'admin-secret',
        ),
      ).toBe(true),
    );
  });

  test('remove sends a DELETE for the chosen model', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('pricey-llm');
    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    await userEvent.click(screen.getByRole('button', { name: /Remove pricey-llm/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => String(u).includes('/models/configured/') && (i as RequestInit)?.method === 'DELETE'),
      ).toBe(true),
    );
  });

  test('reseed triggers the reseed endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    await userEvent.click(screen.getByRole('button', { name: /Reseed from config/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('/models/configured/reseed'))).toBe(true),
    );
  });

  test('shows an error state when the registry request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('nope', { status: 500, headers: { 'Content-Type': 'text/plain' } }),
    );
    renderPage();
    expect(await screen.findByText(/Failed to load the model registry/i)).toBeInTheDocument();
  });

  test('empty capability groups render a no-model hint', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ total: 0, capabilities: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    renderPage();
    // With no configured models, each of the five capability sections renders
    // its own "No model configured for <capability>" hint.
    await waitFor(() =>
      expect(screen.getAllByText(/No model configured for/i)).toHaveLength(5),
    );
    expect(screen.getByText(/No model configured for embeddings/i)).toBeInTheDocument();
  });

  test('does not fetch the registry when no api key is present', async () => {
    useAuthStore.setState({ apiKey: '', tenantId: 't', plan: 'free', isAuthenticated: false });
    const spy = mockFetch();
    renderPage();
    // Query is disabled without an api key, so no group ever populates and every
    // capability section falls back to its empty hint.
    await waitFor(() =>
      expect(screen.getAllByText(/No model configured for/i)).toHaveLength(5),
    );
    expect(spy.mock.calls.some(([u]) => String(u).includes('/models/configured'))).toBe(false);
  });

  test('toggling a capability chip removes it, then toggling another adds it', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));

    // "Reasoning" (text_generation) starts selected in the default form; clicking
    // it exercises the filter branch of toggleCap.
    const reasoningChip = screen.getByRole('button', { name: 'Reasoning' });
    expect(reasoningChip.className).toMatch(/bg-primary/);
    await userEvent.click(reasoningChip);
    expect(reasoningChip.className).not.toMatch(/bg-primary/);

    // Clicking an unselected chip exercises the append branch of toggleCap.
    const visionChip = screen.getByRole('button', { name: 'Vision' });
    expect(visionChip.className).not.toMatch(/bg-primary/);
    await userEvent.click(visionChip);
    expect(visionChip.className).toMatch(/bg-primary/);
  });

  test('editing provider, cost, and the tools/vision checkboxes updates the form', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));

    await userEvent.type(screen.getByPlaceholderText(/openai\/gpt-oss-20b/i), 'gpt-oss-20b');
    const providerInput = screen.getByPlaceholderText(/nvidia \/ openai \/ anthropic/i);
    await userEvent.type(providerInput, 'together');
    const costInput = screen.getByDisplayValue('0');
    await userEvent.clear(costInput);
    await userEvent.type(costInput, '0.0012');

    const toolsCheckbox = screen.getByRole('checkbox', { name: /Supports tools/i });
    const visionCheckbox = screen.getByRole('checkbox', { name: /Supports vision/i });
    expect(toolsCheckbox).toBeChecked();
    expect(visionCheckbox).not.toBeChecked();
    await userEvent.click(toolsCheckbox);
    await userEvent.click(visionCheckbox);
    expect(toolsCheckbox).not.toBeChecked();
    expect(visionCheckbox).toBeChecked();

    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/models/configured') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
    const [, init] = spy.mock.calls.find(
      ([u, i]) => String(u).includes('/models/configured') && (i as RequestInit)?.method === 'POST',
    )!;
    const posted = JSON.parse((init as RequestInit).body as string);
    expect(posted.provider).toBe('together');
    expect(posted.cost_per_1k_input).toBeCloseTo(0.0012);
    expect(posted.supports_tools).toBe(false);
    expect(posted.supports_vision).toBe(true);
  });

  test('selecting the vision capability forces supports_vision on even if unchecked', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));

    await userEvent.type(screen.getByPlaceholderText(/openai\/gpt-oss-20b/i), 'vision-model');
    await userEvent.click(screen.getByRole('button', { name: 'Vision' }));
    expect(screen.getByRole('checkbox', { name: /Supports vision/i })).not.toBeChecked();

    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/models/configured') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
    const [, init] = spy.mock.calls.find(
      ([u, i]) => String(u).includes('/models/configured') && (i as RequestInit)?.method === 'POST',
    )!;
    const posted = JSON.parse((init as RequestInit).body as string);
    expect(posted.capabilities).toEqual(expect.arrayContaining(['text_generation', 'vision']));
    expect(posted.supports_vision).toBe(true);
  });

  test('cancel closes the modal without posting anything', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));
    expect(screen.getByText(/Add \/ override a model/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(screen.queryByText(/Add \/ override a model/i)).not.toBeInTheDocument();
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);
  });

  test('shows the server error message when saving a model fails', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/models/configured') && method === 'POST')
        return new Response(JSON.stringify({ error: { message: 'model_id already registered' } }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        });
      if (url.includes('/models/configured'))
        return new Response(JSON.stringify(REGISTRY), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderPage();
    await screen.findByText('cheap-llm');
    await userEvent.type(screen.getByPlaceholderText(/Platform admin key/i), 'admin-secret');
    await userEvent.click(screen.getByRole('button', { name: /Add Model/i }));
    await userEvent.type(screen.getByPlaceholderText(/openai\/gpt-oss-20b/i), 'dup-model');
    await userEvent.click(screen.getByRole('button', { name: /^Save$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/model_id already registered/i);
    // The modal stays open on failure so the operator can correct and retry.
    expect(screen.getByText(/Add \/ override a model/i)).toBeInTheDocument();
    spy.mockRestore();
  });
});
