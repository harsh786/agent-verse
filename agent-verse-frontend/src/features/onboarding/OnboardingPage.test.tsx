import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { vi, expect, test, beforeEach } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { OnboardingPage } from './OnboardingPage';

test('create-agent step sends X-API-Key from the auth store (not localStorage)', async () => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'store-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/agents/create')) {
      return new Response(JSON.stringify({ agent_id: 'a1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/tenants/me/llm')) {
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>
    </QueryClientProvider>
  );

  // Navigate through steps to reach Step 3 (Create Agent)
  // Step 1: LLM config - click skip by typing a minimal api key and saving
  // We need to navigate to step 3. Let's find and fill the LLM step first.
  // The page starts at step 1. We need to advance to step 3.
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
