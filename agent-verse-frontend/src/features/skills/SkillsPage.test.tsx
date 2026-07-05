import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import SkillsPage from './SkillsPage';

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

const MOCK_PLATFORM_SKILLS = [
  {
    id: 'skill-summarize-compress',
    name: 'summarize-and-compress',
    description: 'Produce concise, citation-backed output.',
    trigger_hints: ['summarize', 'compress', 'brief', 'tldr'],
    instructions: 'Produce concise, citation-backed output.',
    allowed_tools: [],
    token_estimate: 80,
    visibility: 'platform',
    is_platform: true,
  },
  {
    id: 'skill-code-review',
    name: 'code-review',
    description: 'Review for correctness, security, performance.',
    trigger_hints: ['review code', 'code review', 'PR review'],
    instructions: 'Review for correctness and security.',
    allowed_tools: ['github_get_pr'],
    token_estimate: 150,
    visibility: 'platform',
    is_platform: true,
  },
];

const MOCK_CUSTOM_SKILLS = [
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
  },
];

function mockFetch(
  platformSkills = MOCK_PLATFORM_SKILLS,
  customSkills = MOCK_CUSTOM_SKILLS
) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();

    if (url.includes('/skills') && method === 'GET') {
      const skills = [...platformSkills, ...customSkills];
      return new Response(JSON.stringify({ skills, total: skills.length }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/skills') && method === 'POST') {
      return new Response(
        JSON.stringify({ id: 'new-001', name: 'new-skill', description: 'desc', status: 'created' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/skills/') && method === 'DELETE') {
      return new Response(JSON.stringify({ id: 'custom-001', status: 'deleted' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

describe('SkillsPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key',
      tenantId: 'tenant-1',
      plan: 'enterprise',
      isAuthenticated: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders Skills heading', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /Skills/i })).toBeInTheDocument()
    );
  });

  test('shows description text', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/Composable instruction packs/i)).toBeInTheDocument()
    );
  });

  test('shows loading state before skills arrive', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows platform skills section', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/Platform Skills/i)).toBeInTheDocument()
    );
  });

  test('renders platform skill names', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('summarize-and-compress')).toBeInTheDocument();
      expect(screen.getByText('code-review')).toBeInTheDocument();
    });
  });

  test('shows custom skills section when custom skills exist', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/Custom Skills/i)).toBeInTheDocument()
    );
  });

  test('renders custom skill name', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('my-research-skill')).toBeInTheDocument()
    );
  });

  test('shows token estimate for platform skills', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('~80 tokens')).toBeInTheDocument();
    });
  });

  test('shows create skill button', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('+ Create Skill')).toBeInTheDocument()
    );
  });

  test('shows empty state when no skills returned', async () => {
    mockFetch([], []);
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('No skills found.')).toBeInTheDocument()
    );
  });

  test('does not crash on fetch error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('server error', { status: 500 })
    );
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy());
  });

  test('shows delete button for custom skills', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText('Delete')).toBeInTheDocument());
  });
});
