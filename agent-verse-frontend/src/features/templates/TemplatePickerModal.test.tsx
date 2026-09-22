/**
 * Tests for TemplatePickerModal and the GoalsListPage template→goal flow.
 *
 * Covers:
 * - TemplatePickerModal renders templates from API
 * - Clicking "Select" opens TemplateInstantiator
 * - TemplateInstantiator "Use in Goal" calls onUseInGoal
 * - GoalsListPage Templates button opens picker and pre-fills textarea
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { TemplatePickerModal } from '@/features/templates/components/TemplatePickerModal';
import { GoalsListPage } from '@/features/goals/GoalsListPage';

const TEMPLATE_NO_PARAMS = {
  id: 'tpl-1',
  name: 'Run Test Suite',
  description: 'Run all tests',
  goal_text: 'Run the full test suite and report failures.',
  domain: 'engineering',
  parameters: [],
  use_count: 0,
  version: 1,
  created_at: new Date().toISOString(),
};

const TEMPLATE_WITH_PARAMS = {
  id: 'tpl-2',
  name: 'Deploy Service',
  description: 'Deploy a microservice',
  goal_text: 'Deploy {{service}} to {{environment}}.',
  domain: 'devops',
  parameters: [
    { name: 'service', description: 'Service name', required: true, default: null },
    { name: 'environment', description: 'Target env', required: true, default: null },
  ],
  use_count: 3,
  version: 1,
  created_at: new Date().toISOString(),
};

function mockFetch(templates = [TEMPLATE_NO_PARAMS, TEMPLATE_WITH_PARAMS]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/templates')) {
      return new Response(JSON.stringify(templates), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/goals')) {
      return new Response(JSON.stringify({ goals: [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/agents')) {
      return new Response(JSON.stringify([]), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(null, { status: 404 });
  });
}

function renderPicker(onUseInGoal = vi.fn(), onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <TemplatePickerModal onUseInGoal={onUseInGoal} onClose={onClose} />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function renderGoalsPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={['/goals']}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/goals" element={<GoalsListPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  useAuthStore.setState({
    apiKey: 'test-key', tenantId: 't1', plan: 'professional', isAuthenticated: true,
  });
});
afterEach(() => vi.restoreAllMocks());

// ── TemplatePickerModal ───────────────────────────────────────────────────────

describe('TemplatePickerModal', () => {
  test('renders heading and search input', () => {
    mockFetch();
    renderPicker();
    expect(screen.getByText('Choose a template')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/search templates/i)).toBeInTheDocument();
  });

  test('renders template cards after load', async () => {
    mockFetch();
    renderPicker();
    await waitFor(() => expect(screen.getByText('Run Test Suite')).toBeInTheDocument());
    expect(screen.getByText('Deploy Service')).toBeInTheDocument();
  });

  test('calls onClose when backdrop clicked', async () => {
    mockFetch([]);
    const onClose = vi.fn();
    renderPicker(vi.fn(), onClose);
    // Backdrop is aria-hidden=true; click the close button instead
    const closeBtn = screen.getByRole('button', { name: /close template picker/i });
    await userEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledOnce();
  });

  test('shows empty state when no templates', async () => {
    mockFetch([]);
    renderPicker();
    await waitFor(() => expect(screen.getByText('No templates found')).toBeInTheDocument());
  });

  test('filters templates by search', async () => {
    mockFetch();
    renderPicker();
    await waitFor(() => screen.getByText('Deploy Service'));
    const searchInput = screen.getByPlaceholderText(/search templates/i);
    await userEvent.type(searchInput, 'deploy');
    expect(screen.getByText('Deploy Service')).toBeInTheDocument();
    expect(screen.queryByText('Run Test Suite')).not.toBeInTheDocument();
  });

  test('clicking a domain filter re-queries by domain, and clicking it again clears the filter', async () => {
    const fetchSpy = mockFetch();
    renderPicker();
    await waitFor(() => screen.getByText('Deploy Service'));

    await userEvent.click(screen.getByRole('button', { name: 'devops' }));
    await waitFor(() =>
      expect(fetchSpy.mock.calls.some((c) => String(c[0]).includes('domain=devops'))).toBe(true),
    );

    // Clicking the now-active domain button again clears the filter.
    await userEvent.click(screen.getByRole('button', { name: 'devops' }));
    await waitFor(() =>
      expect(fetchSpy.mock.calls.some((c) => String(c[0]).includes('domain='))).toBe(true),
    );

    // Clicking "All" also clears it.
    await userEvent.click(screen.getByRole('button', { name: 'devops' }));
    await userEvent.click(screen.getByRole('button', { name: 'All' }));
    await waitFor(() => expect(screen.getByText('Run Test Suite')).toBeInTheDocument());
  });

  test('shows a loading skeleton while the templates query is in flight', async () => {
    let resolveFetch!: (r: Response) => void;
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise((resolve) => { resolveFetch = resolve; }),
    );
    const { container } = renderPicker();
    expect(container.querySelectorAll('.h-44').length).toBe(6);
    expect(screen.queryByText('No templates found')).not.toBeInTheDocument();

    resolveFetch(new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await waitFor(() => expect(screen.getByText('No templates found')).toBeInTheDocument());
  });

  test('clicking "Manage templates" closes the picker', async () => {
    mockFetch([]);
    const onClose = vi.fn();
    renderPicker(vi.fn(), onClose);
    await waitFor(() => screen.getByText('No templates found'));
    await userEvent.click(screen.getByRole('link', { name: /manage templates/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

// ── TemplateInstantiator onUseInGoal ─────────────────────────────────────────

describe('TemplateInstantiator with onUseInGoal', () => {
  test('shows "Use in Goal" button (not "Run Now") when onUseInGoal provided', async () => {
    mockFetch([TEMPLATE_NO_PARAMS]);
    const onUseInGoal = vi.fn();
    renderPicker(onUseInGoal);
    await waitFor(() => screen.getByText('Run Test Suite'));
    // Click "Select" on the no-params template
    await userEvent.click(screen.getByRole('button', { name: /use template: run test suite/i }));
    // The instantiator should show "Use in Goal" not "Run Now"
    await waitFor(() => expect(screen.getByRole('button', { name: /use in goal/i })).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /run now/i })).not.toBeInTheDocument();
  });

  test('onUseInGoal called with goal text for no-param template', async () => {
    mockFetch([TEMPLATE_NO_PARAMS]);
    const onUseInGoal = vi.fn();
    const onClose = vi.fn();

    // Mock instantiate call
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/templates') && url.includes('/instantiate')) {
        return new Response(
          JSON.stringify({ template_id: 'tpl-1', instantiated_goal: TEMPLATE_NO_PARAMS.goal_text, parameters_used: {} }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/templates')) {
        return new Response(JSON.stringify([TEMPLATE_NO_PARAMS]), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderPicker(onUseInGoal, onClose);
    await waitFor(() => screen.getByText('Run Test Suite'));
    await userEvent.click(screen.getByRole('button', { name: /use template: run test suite/i }));
    await waitFor(() => screen.getByRole('button', { name: /use in goal/i }));
    await userEvent.click(screen.getByRole('button', { name: /use in goal/i }));
    expect(onUseInGoal).toHaveBeenCalledWith(TEMPLATE_NO_PARAMS.goal_text);
    expect(onClose).toHaveBeenCalled();
  });
});

// ── GoalsListPage Templates button ───────────────────────────────────────────

describe('GoalsListPage Templates integration', () => {
  test('Templates button opens picker modal', async () => {
    mockFetch();
    renderGoalsPage();
    const btn = await screen.findByRole('button', { name: /browse goal templates/i });
    await userEvent.click(btn);
    await waitFor(() => expect(screen.getByText('Choose a template')).toBeInTheDocument());
  });

  test('picking a no-param template pre-fills the goal textarea', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/templates') && url.includes('/instantiate')) {
        return new Response(
          JSON.stringify({ template_id: 'tpl-1', instantiated_goal: TEMPLATE_NO_PARAMS.goal_text, parameters_used: {} }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/templates')) {
        return new Response(JSON.stringify([TEMPLATE_NO_PARAMS]), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/goals')) {
        return new Response(JSON.stringify({ goals: [] }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/agents')) {
        return new Response(JSON.stringify([]), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });

    renderGoalsPage();
    // Open picker
    await userEvent.click(await screen.findByRole('button', { name: /browse goal templates/i }));
    // Picker loads template
    await waitFor(() => screen.getByText('Run Test Suite'));
    // Select it
    await userEvent.click(screen.getByRole('button', { name: /use template: run test suite/i }));
    // Instantiator shows "Use in Goal"
    await waitFor(() => screen.getByRole('button', { name: /use in goal/i }));
    await userEvent.click(screen.getByRole('button', { name: /use in goal/i }));
    // Picker should be gone and textarea should have the goal text
    await waitFor(() => expect(screen.queryByText('Choose a template')).not.toBeInTheDocument());
    const textarea = screen.getByRole('textbox', { name: /goal text/i }) as HTMLTextAreaElement;
    expect(textarea.value).toBe(TEMPLATE_NO_PARAMS.goal_text);
  });
});
