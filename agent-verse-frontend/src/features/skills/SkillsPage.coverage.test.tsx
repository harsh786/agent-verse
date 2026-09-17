/**
 * SkillsPage — extra function/branch coverage.
 *
 * SkillsPage.test.tsx and SkillsPage.branches.test.tsx cover the happy paths
 * (headings, sections, search, create/edit/delete/toggle/test success). This
 * file targets what was still uncovered: export, import (success + failure),
 * every mutation's onError branch, the create-form error banner, the
 * disabled-when-no-apiKey query, platform-card no-op handlers, the
 * enabled/disabled toggle glyphs, use_count rendering, and the various modal
 * dismiss (X / Cancel) controls.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
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
    use_count: 7,
  },
  {
    id: 'custom-002',
    name: 'disabled-skill',
    description: 'Not currently enabled',
    trigger_hints: ['dormant'],
    instructions: 'Do nothing right now.',
    allowed_tools: [],
    token_estimate: 20,
    visibility: 'tenant',
    is_platform: false,
    enabled: false,
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

describe('SkillsPage — extra coverage', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'test-key', tenantId: 'tenant-1', plan: 'enterprise', isAuthenticated: true });
    useToastStore.setState({ toasts: [] });
  });
  afterEach(() => vi.restoreAllMocks());

  test('does not fetch skills when there is no apiKey', async () => {
    useAuthStore.setState({ apiKey: '', tenantId: null, plan: 'free', isAuthenticated: false });
    const spy = mockFetch();
    renderPage();
    // Query is disabled, so the loading text never appears and no skills load.
    expect(screen.getByText('No skills found.')).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  test('shows use_count for a skill that has been used', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    expect(screen.getByText('Used 7×')).toBeInTheDocument();
  });

  test('disabled custom skill renders dimmed with an Off toggle', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('disabled-skill');
    expect(screen.getByText('Inactive')).toBeInTheDocument();
    expect(screen.getByText('○ Off')).toBeInTheDocument();
  });

  test('export downloads a JSON blob of all skills', async () => {
    mockFetch();
    const createObjectURL = vi.fn(() => 'blob:mock-url');
    const revokeObjectURL = vi.fn();
    (URL as unknown as { createObjectURL: typeof createObjectURL }).createObjectURL = createObjectURL;
    (URL as unknown as { revokeObjectURL: typeof revokeObjectURL }).revokeObjectURL = revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getByRole('button', { name: /Export/i }));

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
  });

  test('platform card Toggle/Delete are inert no-ops', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('summarize-and-compress');
    // Platform cards render no On/Off toggle and no Delete button at all.
    const platformCard = screen.getByText('summarize-and-compress').closest('div')!
      .closest('div')!.closest('div')!;
    expect(within(platformCard).queryByText(/On|Off/)).not.toBeInTheDocument();
    expect(within(platformCard).queryByText('Delete')).not.toBeInTheDocument();
  });

  test('create-form Cancel resets and hides the form', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getByText('+ Create Skill'));
    await userEvent.type(screen.getByPlaceholderText('my-research-skill'), 'temp-name');
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/ }));
    expect(screen.queryByText('Create Custom Skill')).not.toBeInTheDocument();

    // Reopening shows the field cleared back to empty.
    await userEvent.click(screen.getByText('+ Create Skill'));
    expect(screen.getByPlaceholderText('my-research-skill')).toHaveValue('');
  });

  test('create mutation error shows the inline failure banner and toasts', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/skills') && method === 'GET') {
        return new Response(JSON.stringify({ skills: [...PLATFORM, ...CUSTOM] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/skills') && method === 'POST') {
        return new Response('boom', { status: 500 });
      }
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getByText('+ Create Skill'));
    await userEvent.type(screen.getByPlaceholderText('my-research-skill'), 'brand-new');
    await userEvent.type(screen.getByPlaceholderText(/Always cross-reference/i), 'Do it.');
    await userEvent.click(screen.getByRole('button', { name: /^Create$/ }));

    await waitFor(() =>
      expect(screen.getByText('Failed to create skill. Please try again.')).toBeInTheDocument()
    );
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some(t => t.kind === 'error' && t.message.includes('Failed to create skill'))
      ).toBe(true)
    );
  });

  test('edit modal X button closes without saving', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getAllByRole('button', { name: /Edit/i })[0]);
    await screen.findByText('Edit Skill');
    const closeButtons = screen.getAllByRole('button');
    const xButton = closeButtons.find(b => b.querySelector('svg') && b.textContent === '')!;
    await userEvent.click(xButton);
    expect(screen.queryByText('Edit Skill')).not.toBeInTheDocument();
  });

  test('edit mutation error toasts a failure message', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/skills') && method === 'GET') {
        return new Response(JSON.stringify({ skills: [...PLATFORM, ...CUSTOM] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (method === 'PUT') return new Response('nope', { status: 500 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getAllByRole('button', { name: /Edit/i })[0]);
    await screen.findByText('Edit Skill');
    await userEvent.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some(t => t.kind === 'error' && t.message.startsWith('Failed to update skill'))
      ).toBe(true)
    );
  });

  test('delete mutation error toasts a failure message', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/skills') && method === 'GET') {
        return new Response(JSON.stringify({ skills: [...PLATFORM, ...CUSTOM] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (method === 'DELETE') return new Response('nope', { status: 500 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getAllByRole('button', { name: /Delete/i })[0]);

    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some(t => t.kind === 'error' && t.message.startsWith('Failed to delete skill'))
      ).toBe(true)
    );
  });

  test('toggle mutation error toasts a failure message', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/skills') && method === 'GET') {
        return new Response(JSON.stringify({ skills: [...PLATFORM, ...CUSTOM] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (method === 'PATCH') return new Response('nope', { status: 500 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getByText(/On/));

    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some(t => t.kind === 'error' && t.message.startsWith('Toggle failed'))
      ).toBe(true)
    );
  });

  test('test modal error path renders the error text and X closes it', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/skills') && method === 'GET') {
        return new Response(JSON.stringify({ skills: [...PLATFORM, ...CUSTOM] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/test')) return new Response('nope', { status: 500 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.click(screen.getAllByRole('button', { name: /Test/i })[0]);
    const heading = await screen.findByText(/^Test:/);
    const modal = heading.closest('div')!.parentElement as HTMLElement;
    await userEvent.type(within(modal).getByPlaceholderText(/Enter test input/i), 'go');
    await userEvent.click(within(modal).getByRole('button', { name: /Run Test/i }));

    expect(await screen.findByText(/Error: Error: 500/)).toBeInTheDocument();

    await userEvent.click(within(modal).getByRole('button', { name: '' }));
    expect(screen.queryByText(/^Test:/)).not.toBeInTheDocument();
  });

  test('import parses a file, POSTs non-platform skills, and invalidates', async () => {
    const spy = mockFetch();
    const { container } = renderPage();
    await screen.findByText('my-research-skill');

    const importedSkills = [
      { ...CUSTOM[0], id: 'imported-1', name: 'imported-skill', is_platform: false },
      { ...PLATFORM[0], id: 'platform-imported', is_platform: true },
    ];
    const file = new File([JSON.stringify(importedSkills)], 'skills-export.json', {
      type: 'application/json',
    });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some(t => t.kind === 'success' && t.message === 'Imported 1 skill(s)')
      ).toBe(true)
    );
    // Only the non-platform skill should have been POSTed.
    expect(
      spy.mock.calls.filter(
        ([u, i]) => /\/skills$/.test(String(u)) && (i as RequestInit)?.method === 'POST'
      )
    ).toHaveLength(1);
  });

  test('import with malformed JSON toasts an import failure', async () => {
    mockFetch();
    const { container } = renderPage();
    await screen.findByText('my-research-skill');

    const file = new File(['not json{{{'], 'bad.json', { type: 'application/json' });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some(t => t.kind === 'error' && t.message.startsWith('Import failed'))
      ).toBe(true)
    );
  });

  test('import with a non-array payload treats it as zero skills', async () => {
    mockFetch();
    const { container } = renderPage();
    await screen.findByText('my-research-skill');

    const file = new File([JSON.stringify({ not: 'an array' })], 'weird.json', {
      type: 'application/json',
    });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some(t => t.kind === 'success' && t.message === 'Imported 0 skill(s)')
      ).toBe(true)
    );
  });

  test('search matches on description text, not just name', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.type(screen.getByPlaceholderText(/Search skills/i), 'web research quality');
    await waitFor(() => expect(screen.getByText('my-research-skill')).toBeInTheDocument());
    expect(screen.queryByText('summarize-and-compress')).not.toBeInTheDocument();
  });

  test('search with no custom matches shows the "no match" empty message', async () => {
    mockFetch();
    renderPage();
    await screen.findByText('my-research-skill');
    await userEvent.type(screen.getByPlaceholderText(/Search skills/i), 'zzz-nonexistent');
    await waitFor(() =>
      expect(screen.getByText('No custom skills match your search.')).toBeInTheDocument()
    );
    expect(screen.getByText('No platform skills match your search.')).toBeInTheDocument();
  });
});
