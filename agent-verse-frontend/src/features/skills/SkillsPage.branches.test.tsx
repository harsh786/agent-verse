/**
 * SkillsPage — companion branch coverage.
 *
 * SkillsPage.test.tsx covers headings, sections, names, token estimate, empty
 * and error states. This file exercises the untested interactions: search
 * filtering, the create form, the edit modal, the test modal, and the
 * toggle/delete mutations — asserting the right HTTP method + endpoint fires.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import SkillsPage from './SkillsPage';

const PLATFORM = [
  {
    id: 'skill-summarize',
    name: 'summarize-and-compress',
    description: 'Produce concise, citation-backed output.',
    trigger_hints: ['summarize', 'compress', 'brief', 'tldr'],
    instructions: 'Produce concise output.',
    allowed_tools: [],
    token_estimate: 80,
    visibility: 'platform',
    is_platform: true,
  },
  {
    id: 'skill-code-review',
    name: 'code-review',
    description: 'Review for correctness and security.',
    trigger_hints: ['code review'],
    instructions: 'Review for correctness.',
    allowed_tools: ['github_get_pr'],
    token_estimate: 150,
    visibility: 'platform',
    is_platform: true,
  },
];

const CUSTOM = [
  {
    id: 'custom-001',
    name: 'my-research-skill',
    description: 'Improves web research quality',
    trigger_hints: ['research', 'search'],
    instructions: 'Always cross-reference at least 3 sources.',
    allowed_tools: ['web_search'],
    token_estimate: 100,
    visibility: 'tenant',
    is_platform: false,
    enabled: true,
  },
];

function mockFetch(platform = PLATFORM, custom = CUSTOM) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/skills') && method === 'GET') {
      const skills = [...platform, ...custom];
      return new Response(JSON.stringify({ skills, total: skills.length }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/test') && method === 'POST') {
      return new Response(JSON.stringify({ matched: true, output: 'ran ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    // create / update / delete / toggle
    return new Response(JSON.stringify({ id: 'x', status: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SkillsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('SkillsPage — branches', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', plan: 'enterprise', isAuthenticated: true });
  });
  afterEach(() => vi.restoreAllMocks());

  test('collapses trigger-hint chips beyond three into a +N indicator', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('summarize-and-compress');
    // summarize-and-compress has 4 hints → shows 3 chips + "+1".
    expect(screen.getByText('+1')).toBeInTheDocument();
  });

  test('renders the allowed-tools count per skill', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('code-review');
    // code-review and my-research-skill each expose exactly one tool.
    expect(screen.getAllByText('1 tool').length).toBeGreaterThanOrEqual(1);
    // summarize-and-compress exposes none.
    expect(screen.getByText('0 tools')).toBeInTheDocument();
  });

  test('search narrows the visible skills and updates the section count', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('summarize-and-compress');
    await userEvent.type(screen.getByPlaceholderText(/Search skills/i), 'code');
    await waitFor(() => expect(screen.queryByText('summarize-and-compress')).not.toBeInTheDocument());
    expect(screen.getByText('code-review')).toBeInTheDocument();
    expect(screen.getByText(/Platform Skills \(\s*1\s*of\s*2\s*\)/)).toBeInTheDocument();
  });

  test('clear-search button restores the full list', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('summarize-and-compress');
    const searchInput = screen.getByPlaceholderText(/Search skills/i);
    await userEvent.type(searchInput, 'code');
    await waitFor(() => expect(screen.queryByText('summarize-and-compress')).not.toBeInTheDocument());
    // The clear (X) button appears only while there is search text; it is the
    // only <button> inside the search bar row alongside the input.
    await userEvent.click(searchInput.parentElement!.querySelector('button')!);
    expect(await screen.findByText('summarize-and-compress')).toBeInTheDocument();
  });

  test('create form is gated then POSTs a new skill', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('summarize-and-compress');
    await userEvent.click(screen.getByText('+ Create Skill'));

    expect(screen.getByText('Create Custom Skill')).toBeInTheDocument();
    const createBtn = screen.getByRole('button', { name: /^Create$/ });
    expect(createBtn).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText('my-research-skill'), 'brand-new-skill');
    await userEvent.type(
      screen.getByPlaceholderText(/Always cross-reference/i),
      'Do the thing carefully.'
    );
    expect(createBtn).toBeEnabled();

    await userEvent.click(createBtn);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => /\/skills$/.test(String(u)) && (i as RequestInit)?.method === 'POST'
        )
      ).toBe(true)
    );
  });

  test('edit modal opens prefilled and PUTs the updated skill', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getByRole('button', { name: /Edit/i }));

    const modal = await screen.findByText('Edit Skill');
    expect(modal).toBeInTheDocument();
    const nameInput = screen.getByDisplayValue('my-research-skill');
    await userEvent.type(nameInput, '-v2');
    await userEvent.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => /\/skills\/custom-001$/.test(String(u)) && (i as RequestInit)?.method === 'PUT'
        )
      ).toBe(true)
    );
  });

  test('delete on a custom skill fires a DELETE for that id', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getByRole('button', { name: /Delete/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => /\/skills\/custom-001$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE'
        )
      ).toBe(true)
    );
  });

  test('toggling a custom skill PATCHes the enabled flag', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    // The custom card renders a "● On" toggle for enabled skills.
    await userEvent.click(screen.getByText(/On/));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => /\/skills\/custom-001$/.test(String(u)) && (i as RequestInit)?.method === 'PATCH'
        )
      ).toBe(true)
    );
  });

  test('test modal runs a skill and renders the JSON result', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    // Open the test modal on the custom skill (use the first Test button).
    await userEvent.click(screen.getAllByRole('button', { name: /Test/i })[0]);

    const heading = await screen.findByText(/^Test:/);
    const modal = heading.closest('div')!.parentElement as HTMLElement;
    await userEvent.type(within(modal).getByPlaceholderText(/Enter test input/i), 'try this');
    await userEvent.click(within(modal).getByRole('button', { name: /Run Test/i }));

    expect(await screen.findByText(/ran ok/)).toBeInTheDocument();
  });

  test('export button is disabled when there are no skills', async () => {
    mockFetch([], []);
    renderPage();
    await screen.findByText('No skills found.');
    expect(screen.getByRole('button', { name: /Export/i })).toBeDisabled();
  });
});
