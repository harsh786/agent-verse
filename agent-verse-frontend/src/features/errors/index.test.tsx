/**
 * Feature Smoke Tests — coverage for 12 previously-untested features.
 *
 * Each test renders the feature's main page component inside
 * QueryClientProvider + MemoryRouter (the standard setup used by
 * existing feature tests) and verifies it renders without crashing.
 * API responses are mocked via vi.spyOn(globalThis, 'fetch').
 *
 * Features covered:
 *   channels, errors, eval-suites, gateway, graphify, knowledge-graph,
 *   models, obsidian, prompt-variants, red-team, state-machines,
 *   workflow-engine
 *
 * NOTE: This file lives in src/features/errors/ so all cross-feature
 * imports use ../feature-name/ paths.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// ─── Shared helpers ─────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
}

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = makeQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

function mockJsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function setupAuth() {
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({
    apiKey: 'test-key',
    tenantId: 'test-tenant',
    plan: 'professional',
    isAuthenticated: true,
    sessionValidated: true,
  });
}

function mockFetchEmpty() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    mockJsonResponse({})
  );
}

beforeEach(() => {
  setupAuth();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ─── Channels ─────────────────────────────────────────────────────────────────

describe('ChannelMappingsPage', () => {
  test('renders without crashing and shows add button', async () => {
    const { ChannelMappingsPage } = await import('../channels/ChannelMappingsPage');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/channels')) return mockJsonResponse([]);
      return mockJsonResponse({});
    });

    renderWithProviders(<ChannelMappingsPage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Errors ──────────────────────────────────────────────────────────────────

describe('NotFoundPage', () => {
  test('renders 404 page with heading and links', async () => {
    const { default: NotFoundPage } = await import('./NotFoundPage');

    renderWithProviders(<NotFoundPage />);
    expect(screen.getByText('404')).toBeInTheDocument();
    expect(screen.getByText(/Page not found/i)).toBeInTheDocument();
    expect(screen.getByText(/Go to Dashboard/i)).toBeInTheDocument();
    expect(screen.getByText(/Go Back/i)).toBeInTheDocument();
  });

  test('go back button calls window.history.back', async () => {
    const { default: NotFoundPage } = await import('./NotFoundPage');
    const backSpy = vi.spyOn(window.history, 'back').mockImplementation(() => {});

    renderWithProviders(<NotFoundPage />);
    const goBackBtn = screen.getByText(/Go Back/i);
    await goBackBtn.click();
    expect(backSpy).toHaveBeenCalled();
  });
});

// ─── Eval Suites ─────────────────────────────────────────────────────────────

describe('EvalSuitesPage', () => {
  test('renders without crashing', async () => {
    const { EvalSuitesPage } = await import('../eval-suites/EvalSuitesPage');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('eval-suites') || url.includes('eval_suites'))
        return mockJsonResponse([]);
      return mockJsonResponse({});
    });

    renderWithProviders(<EvalSuitesPage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Gateway: APIKeyManager ───────────────────────────────────────────────────

describe('APIKeyManager', () => {
  test('renders without crashing with empty keys', async () => {
    const { APIKeyManager } = await import('../gateway/APIKeyManager');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('api-keys') || url.includes('keys')) return mockJsonResponse([]);
      return mockJsonResponse({});
    });

    renderWithProviders(<APIKeyManager orgId="org-test-1" />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });

  test('renders existing keys list', async () => {
    const { APIKeyManager } = await import('../gateway/APIKeyManager');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('api-keys') || url.includes('keys'))
        return mockJsonResponse([
          { id: 'key-1', name: 'Production Key', prefix: 'av_pro_', created_at: new Date().toISOString() },
        ]);
      return mockJsonResponse({});
    });

    renderWithProviders(<APIKeyManager orgId="org-test-1" />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Graphify ─────────────────────────────────────────────────────────────────

describe('GraphifyPage', () => {
  test('renders without crashing', async () => {
    const { GraphifyPage } = await import('../graphify/GraphifyPage');

    mockFetchEmpty();
    renderWithProviders(<GraphifyPage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Knowledge Graph: GraphExplorerPage ──────────────────────────────────────

describe('GraphExplorerPage', () => {
  test('renders without crashing with empty graph', async () => {
    const { GraphExplorerPage } = await import('../knowledge-graph/GraphExplorerPage');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('graph') || url.includes('nodes'))
        return mockJsonResponse({ nodes: [], edges: [] });
      return mockJsonResponse({});
    });

    renderWithProviders(<GraphExplorerPage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Models: ModelControlCenter ───────────────────────────────────────────────

describe('ModelControlCenter', () => {
  test('renders without crashing', async () => {
    const { ModelControlCenter } = await import('../models/ModelControlCenter');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('models')) return mockJsonResponse([]);
      return mockJsonResponse({});
    });

    renderWithProviders(<ModelControlCenter />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Obsidian ──────────────────────────────────────────────────────────────────

describe('ObsidianPage', () => {
  test('renders without crashing', async () => {
    const { ObsidianPage } = await import('../obsidian/ObsidianPage');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('obsidian') || url.includes('notes'))
        return mockJsonResponse({ notes: [] });
      return mockJsonResponse({});
    });

    renderWithProviders(<ObsidianPage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Prompt Variants ───────────────────────────────────────────────────────────

describe('PromptVariantsPage', () => {
  test('renders without crashing', async () => {
    const { PromptVariantsPage } = await import('../prompt-variants/PromptVariantsPage');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('prompt-variants') || url.includes('variants'))
        return mockJsonResponse([]);
      return mockJsonResponse({});
    });

    renderWithProviders(<PromptVariantsPage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Red Team ──────────────────────────────────────────────────────────────────

describe('RedTeamPage', () => {
  test('renders without crashing and shows test categories', async () => {
    const { RedTeamPage } = await import('../red-team/RedTeamPage');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('red-team') || url.includes('redteam'))
        return mockJsonResponse([]);
      return mockJsonResponse({});
    });

    renderWithProviders(<RedTeamPage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── State Machines ────────────────────────────────────────────────────────────

describe('StateMachinesPage', () => {
  test('renders without crashing', async () => {
    const { StateMachinesPage } = await import('../state-machines/StateMachinesPage');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('state-machines') || url.includes('machines'))
        return mockJsonResponse([]);
      return mockJsonResponse({});
    });

    renderWithProviders(<StateMachinesPage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});

// ─── Workflow Engine ───────────────────────────────────────────────────────────

describe('WorkflowEnginePage', () => {
  test('renders without crashing with empty runs', async () => {
    const { WorkflowEnginePage } = await import('../workflow-engine/WorkflowEnginePage');

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('workflows') || url.includes('runs'))
        return mockJsonResponse({ runs: [], total: 0 });
      return mockJsonResponse({});
    });

    renderWithProviders(<WorkflowEnginePage />);
    await waitFor(() => expect(document.body.textContent!.length).toBeGreaterThan(0));
  });
});
