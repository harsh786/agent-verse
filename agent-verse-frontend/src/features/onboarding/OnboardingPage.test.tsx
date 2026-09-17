import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { vi, expect, test, beforeEach } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { OnboardingPage } from './OnboardingPage';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  mockNavigate.mockClear();
  useAuthStore.setState({ apiKey: 'store-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('create-agent step sends X-API-Key from the auth store (not localStorage)', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/agents/create')) {
      return jsonResponse({ agent_id: 'a1' });
    }
    if (url.includes('/tenants/me/llm')) {
      return jsonResponse({});
    }
    return jsonResponse({});
  });

  renderPage();

  // Step 1: fill apiKey and save
  const apiKeyInput = screen.getByPlaceholderText(/your openai api key/i);
  await userEvent.type(apiKeyInput, 'fake-llm-key');
  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));

  // Step 2: connector - skip
  await waitFor(() => expect(screen.getByRole('button', { name: /skip for now/i })).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /skip for now/i }));

  // Step 3: create agent
  await waitFor(() => expect(screen.getByRole('button', { name: /create agent/i })).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /create agent/i }));

  await waitFor(() => {
    const call = f.mock.calls.find(([u]) => String(u).includes('/agents/create'));
    expect(call).toBeTruthy();
    expect((call?.[1] as RequestInit)?.headers).toMatchObject({ 'X-API-Key': 'store-key' });
  });
});

test('Step 1 and Step 2: editing provider, model, and connector fields updates their values', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/connectors')) return jsonResponse({ id: 'c1' });
    return jsonResponse({});
  });
  renderPage();

  const providerSelect = screen.getByRole('combobox');
  await userEvent.selectOptions(providerSelect, 'anthropic');
  expect((providerSelect as HTMLSelectElement).value).toBe('anthropic');

  const modelInput = screen.getByPlaceholderText(/gpt-4o/i);
  await userEvent.clear(modelInput);
  await userEvent.type(modelInput, 'claude-3');
  expect((modelInput as HTMLInputElement).value).toBe('claude-3');

  await userEvent.type(screen.getByPlaceholderText(/your anthropic api key/i), 'k');
  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));

  await waitFor(() => expect(screen.getByRole('button', { name: /register & continue/i })).toBeInTheDocument());

  const nameInput = screen.getByPlaceholderText(/^github$/i);
  await userEvent.clear(nameInput);
  await userEvent.type(nameInput, 'My Connector');
  expect((nameInput as HTMLInputElement).value).toBe('My Connector');

  const urlInput = screen.getByPlaceholderText(/^https:\/\/\.\.\.$/i);
  await userEvent.clear(urlInput);
  await userEvent.type(urlInput, 'https://mcp.example.com');
  expect((urlInput as HTMLInputElement).value).toBe('https://mcp.example.com');

  const tokenInput = screen.getByPlaceholderText(/your credentials/i);
  await userEvent.type(tokenInput, 'secret-token');
  expect((tokenInput as HTMLInputElement).value).toBe('secret-token');

  await userEvent.click(screen.getByRole('button', { name: /register & continue/i }));

  await waitFor(() => {
    const call = f.mock.calls.find(([u]) => String(u).includes('/connectors') && !String(u).includes('/connectors/'));
    expect(call).toBeTruthy();
    const body = JSON.parse((call?.[1] as RequestInit).body as string);
    expect(body).toMatchObject({
      name: 'My Connector',
      url: 'https://mcp.example.com',
      auth_config: { token: 'secret-token' },
    });
  });
});

test('Step 1: shows validation error when saving without an API key', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse({}));
  renderPage();

  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));

  expect(await screen.findByText(/api key is required/i)).toBeInTheDocument();
  // No network call should have been made since validation failed client-side.
  expect(f.mock.calls.find(([u]) => String(u).includes('/tenants/me/llm'))).toBeUndefined();
});

test('Step 1: shows the server error message when saving the LLM config fails', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) {
      return jsonResponse({ detail: 'bad key' }, 500);
    }
    return jsonResponse({});
  });
  renderPage();

  const apiKeyInput = screen.getByPlaceholderText(/your openai api key/i);
  await userEvent.type(apiKeyInput, 'fake-llm-key');
  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));

  expect(await screen.findByText(/error/i)).toBeInTheDocument();
  // Still on step 1 — save button remains present (never advanced to step 2).
  expect(screen.queryByRole('button', { name: /register & continue/i })).not.toBeInTheDocument();
});

test('Step 2: hides the token field when auth type is "none" and registers the connector', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/connectors')) return jsonResponse({ id: 'c1' });
    return jsonResponse({});
  });
  renderPage();

  await userEvent.type(screen.getByPlaceholderText(/your openai api key/i), 'k');
  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));

  await waitFor(() => expect(screen.getByRole('button', { name: /register & continue/i })).toBeInTheDocument());

  expect(screen.getByPlaceholderText(/your credentials/i)).toBeInTheDocument();

  const authTypeSelect = screen.getByRole('combobox');
  await userEvent.selectOptions(authTypeSelect, 'none');

  expect(screen.queryByPlaceholderText(/your credentials/i)).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /register & continue/i }));

  await waitFor(() => {
    const call = f.mock.calls.find(([u]) => String(u).includes('/connectors') && !String(u).includes('/connectors/'));
    expect(call).toBeTruthy();
  });
  expect(await screen.findByText(/connector registered!/i)).toBeInTheDocument();
});

test('Step 2: shows an error message when registering the connector fails', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/connectors')) return jsonResponse({ detail: 'boom' }, 500);
    return jsonResponse({});
  });
  renderPage();

  await userEvent.type(screen.getByPlaceholderText(/your openai api key/i), 'k');
  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));

  await waitFor(() => expect(screen.getByRole('button', { name: /register & continue/i })).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /register & continue/i }));

  expect(await screen.findByText(/error/i)).toBeInTheDocument();
});

test('Step 3: create-agent button is disabled while the description is blank', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    return jsonResponse({});
  });
  renderPage();

  await userEvent.type(screen.getByPlaceholderText(/your openai api key/i), 'k');
  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));
  await waitFor(() => expect(screen.getByRole('button', { name: /skip for now/i })).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /skip for now/i }));

  await waitFor(() => expect(screen.getByRole('button', { name: /create agent/i })).toBeInTheDocument());
  const textarea = screen.getByPlaceholderText(/a helpful assistant that reviews prs/i);
  await userEvent.clear(textarea);

  expect(screen.getByRole('button', { name: /create agent/i })).toBeDisabled();
});

test('Step 3: shows an error message when creating the agent fails', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/agents/create')) return jsonResponse({ detail: 'nope' }, 500);
    return jsonResponse({});
  });
  renderPage();

  await userEvent.type(screen.getByPlaceholderText(/your openai api key/i), 'k');
  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));
  await waitFor(() => expect(screen.getByRole('button', { name: /skip for now/i })).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /skip for now/i }));

  await waitFor(() => expect(screen.getByRole('button', { name: /create agent/i })).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /create agent/i }));

  expect(await screen.findByText(/error/i)).toBeInTheDocument();
});

/** Drives the wizard from step 1 through to step 4 (Run First Goal). */
async function advanceToStep4(agentId = 'a1') {
  await userEvent.type(screen.getByPlaceholderText(/your openai api key/i), 'k');
  await userEvent.click(screen.getByRole('button', { name: /save & continue/i }));
  await waitFor(() => expect(screen.getByRole('button', { name: /skip for now/i })).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /skip for now/i }));
  await waitFor(() => expect(screen.getByRole('button', { name: /create agent/i })).toBeInTheDocument());
  await userEvent.click(screen.getByRole('button', { name: /create agent/i }));
  await waitFor(() => expect(screen.getByRole('button', { name: /run goal/i })).toBeInTheDocument());
  return agentId;
}

test('Step 4: submitting a goal shows the goal id and both follow-up actions', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/agents/create')) return jsonResponse({ agent_id: 'agent-42' });
    if (url.endsWith('/goals')) return jsonResponse({ goal_id: 'goal-99' });
    return jsonResponse({});
  });
  renderPage();
  await advanceToStep4();

  await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

  expect(await screen.findByText(/goal submitted!/i)).toBeInTheDocument();
  expect(screen.getByText(/goal-99/)).toBeInTheDocument();

  const dashboardBtn = screen.getByRole('button', { name: /^go to dashboard$/i });
  const watchBtn = screen.getByRole('button', { name: /watch goal/i });
  expect(dashboardBtn).toBeInTheDocument();
  expect(watchBtn).toBeInTheDocument();
});

test('Step 4: falls back to res.id when goal_id is absent, and hides the Goal ID / Watch Goal when neither is present', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/agents/create')) return jsonResponse({ agent_id: 'agent-42' });
    if (url.endsWith('/goals')) return jsonResponse({ id: 'goal-from-id' });
    return jsonResponse({});
  });
  renderPage();
  await advanceToStep4();

  await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

  expect(await screen.findByText(/goal submitted!/i)).toBeInTheDocument();
  expect(screen.getByText(/goal-from-id/)).toBeInTheDocument();
});

test('Step 4: renders without a Goal ID / Watch Goal button when the response has neither goal_id nor id', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/agents/create')) return jsonResponse({ agent_id: 'agent-42' });
    if (url.endsWith('/goals')) return jsonResponse({});
    return jsonResponse({});
  });
  renderPage();
  await advanceToStep4();

  await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

  expect(await screen.findByText(/goal submitted!/i)).toBeInTheDocument();
  expect(screen.queryByText(/goal id:/i)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /watch goal/i })).not.toBeInTheDocument();

  const successPanel = screen.getByText(/goal submitted!/i).closest('div') as HTMLElement;
  expect(within(successPanel.parentElement as HTMLElement).getByRole('button', { name: /^go to dashboard$/i })).toBeInTheDocument();
});

test('Step 4: shows an error message when goal submission fails, without leaving the form', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/agents/create')) return jsonResponse({ agent_id: 'agent-42' });
    if (url.endsWith('/goals')) return jsonResponse({ detail: 'submission failed' }, 500);
    return jsonResponse({});
  });
  renderPage();
  await advanceToStep4();

  await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

  expect(await screen.findByText(/error/i)).toBeInTheDocument();
  // Still on the pre-submit form: the goal textarea remains present.
  expect(screen.getByRole('textbox')).toBeInTheDocument();
  expect(screen.queryByText(/goal submitted!/i)).not.toBeInTheDocument();
});

test('Step 4: run-goal button is disabled once the goal text is cleared', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/agents/create')) return jsonResponse({ agent_id: 'agent-42' });
    return jsonResponse({});
  });
  renderPage();
  await advanceToStep4();

  const textarea = screen.getByRole('textbox');
  await userEvent.clear(textarea);

  expect(screen.getByRole('button', { name: /run goal/i })).toBeDisabled();
});

test('Step 4: clicking "Go to Dashboard" after submitting navigates to /dashboard', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/agents/create')) return jsonResponse({ agent_id: 'agent-42' });
    if (url.endsWith('/goals')) return jsonResponse({ goal_id: 'goal-1' });
    return jsonResponse({});
  });

  renderPage();
  await advanceToStep4();
  await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

  await screen.findByText(/goal submitted!/i);
  await userEvent.click(screen.getByRole('button', { name: /^go to dashboard$/i }));

  expect(mockNavigate).toHaveBeenCalledWith('/dashboard');
});

test('Step 4: clicking "Watch Goal" after submitting navigates to /goals/:id', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/tenants/me/llm')) return jsonResponse({});
    if (url.includes('/agents/create')) return jsonResponse({ agent_id: 'agent-42' });
    if (url.endsWith('/goals')) return jsonResponse({ goal_id: 'goal-7' });
    return jsonResponse({});
  });

  renderPage();
  await advanceToStep4();
  await userEvent.click(screen.getByRole('button', { name: /run goal/i }));

  await screen.findByText(/goal submitted!/i);
  await userEvent.click(screen.getByRole('button', { name: /watch goal/i }));

  expect(mockNavigate).toHaveBeenCalledWith('/goals/goal-7');
});

test('"Skip setup and go to dashboard" link navigates away from onboarding at any step', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => jsonResponse({}));
  renderPage();

  await userEvent.click(screen.getByRole('button', { name: /skip setup and go to dashboard/i }));

  expect(mockNavigate).toHaveBeenCalledWith('/dashboard');
});
