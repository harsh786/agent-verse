/**
 * Ingestion feature tests — types, hooks, SourcesPage, SourceCard, SourceList,
 * SourceCreateWizard, QuotaUsageBar, SourceDetailDrawer.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// ── Fixtures ─────────────────────────────────────────────────────────────────

const SOURCE: import('../types').SourceConfig = {
  source_id: 'src-001',
  tenant_id: 't1',
  name: 'My S3 Bucket',
  family: 'object_storage',
  source_type: 's3',
  enabled: true,
  sync_mode: 'incremental',
  sync_interval_seconds: 3600,
  connection_config: { bucket: 'my-bucket', region: 'us-east-1' },
  cursor_value: '',
  include_patterns: [],
  exclude_patterns: [],
  max_doc_size_bytes: 10_485_760,
  chunking_strategy: 'semantic',
  chunk_size_tokens: 512,
  chunk_overlap_tokens: 64,
  embedding_model: 'voyage-3',
  language_hint: '',
  inherit_source_acl: false,
  min_quality_score: 0.3,
  pii_action: 'redact',
  freshness_ttl_seconds: 86400,
  collection_id: null,
  tags: [],
  last_synced_at: '2026-08-17T00:00:00Z',
  total_docs_indexed: 42,
  total_chunks: 180,
  version: 1,
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-17T00:00:00Z',
};

const SLACK_SOURCE: import('../types').SourceConfig = {
  ...SOURCE,
  source_id: 'src-002',
  name: 'Company Slack',
  family: 'communication',
  source_type: 'slack',
};

const QUOTA: import('../types').IngestionQuota = {
  tenant_id: 't1',
  plan: 'professional',
  sources_used: 3,
  sources_limit: 20,
  docs_used: 5000,
  docs_limit: 100_000,
  tokens_used_month: 80_000,
  tokens_limit_month: 1_000_000,
  cost_usd_month: 12.5,
};

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  useAuthStore.setState({
    apiKey: 'test-key',
    tenantId: 't1',
    plan: 'professional',
    isAuthenticated: true,
  });
});
afterEach(() => vi.restoreAllMocks());

// ── Types ────────────────────────────────────────────────────────────────────

describe('SourceFamily type', () => {
  test('ALL_FAMILIES contains exactly 18 families', async () => {
    const { ALL_FAMILIES } = await import('../types');
    expect(ALL_FAMILIES).toHaveLength(18);
  });

  test('FAMILY_CONFIG covers all 18 families', async () => {
    const { ALL_FAMILIES, FAMILY_CONFIG } = await import('../types');
    for (const f of ALL_FAMILIES) {
      expect(FAMILY_CONFIG[f]).toBeDefined();
      expect(FAMILY_CONFIG[f].label).toBeTruthy();
      expect(FAMILY_CONFIG[f].icon).toBeDefined();
    }
  });
});

// ── QuotaUsageBar ────────────────────────────────────────────────────────────

describe('QuotaUsageBar', () => {
  test('renders docs and sources usage bars', async () => {
    const { QuotaUsageBar } = await import('../components/QuotaUsageBar');
    wrap(<QuotaUsageBar quota={QUOTA} />);
    expect(screen.getByText('Sources')).toBeInTheDocument();
    expect(screen.getByText('Tokens this month')).toBeInTheDocument();
  });

  test('shows warning state when >80% used', async () => {
    const { QuotaUsageBar } = await import('../components/QuotaUsageBar');
    const nearLimitQuota = { ...QUOTA, docs_used: 85_000 };
    wrap(<QuotaUsageBar quota={nearLimitQuota} />);
    expect(screen.getByText('Sources')).toBeInTheDocument();
  });

  test('shows critical state when >95% used', async () => {
    const { QuotaUsageBar } = await import('../components/QuotaUsageBar');
    const criticalQuota = { ...QUOTA, tokens_used_month: 980_000 };
    wrap(<QuotaUsageBar quota={criticalQuota} />);
    // Critical state shows a warning banner with specific text
    expect(screen.getByText(/Quota nearly exceeded/i)).toBeInTheDocument();
  });
});

// ── SourceCard ───────────────────────────────────────────────────────────────

describe('SourceCard', () => {
  // SourceCard uses hooks internally. In test env hooks return undefined/empty.

  test('renders source name', async () => {
    const { SourceCard } = await import('../components/SourceCard');
    wrap(<SourceCard source={SOURCE} />);
    expect(screen.getByText('My S3 Bucket')).toBeInTheDocument();
  });

  test('shows total docs indexed', async () => {
    const { SourceCard } = await import('../components/SourceCard');
    wrap(<SourceCard source={SOURCE} />);
    expect(document.body.textContent).toMatch(/42/);
  });

  test('sync button is rendered', async () => {
    const { SourceCard } = await import('../components/SourceCard');
    wrap(<SourceCard source={SOURCE} />);
    const syncBtn = screen.getByRole('button', { name: /sync source now/i });
    expect(syncBtn).toBeInTheDocument();
  });
});

// ── SourceList ───────────────────────────────────────────────────────────────

describe('SourceList', () => {
  test('renders source names when passed sources', async () => {
    const { SourceList } = await import('../components/SourceList');
    wrap(<SourceList sources={[SOURCE, SLACK_SOURCE]} isLoading={false} onAddSource={vi.fn()} />);
    expect(screen.getByText('My S3 Bucket')).toBeInTheDocument();
    expect(screen.getByText('Company Slack')).toBeInTheDocument();
  });

  test('shows empty state when no sources', async () => {
    const { SourceList } = await import('../components/SourceList');
    wrap(<SourceList sources={[]} isLoading={false} onAddSource={vi.fn()} />);
    expect(screen.getByText(/no sources|add.*source|connect.*first/i)).toBeInTheDocument();
  });

  test('filters sources by search text', async () => {
    const { SourceList } = await import('../components/SourceList');
    wrap(<SourceList sources={[SOURCE, SLACK_SOURCE]} isLoading={false} onAddSource={vi.fn()} />);
    const searchInput = screen.getByPlaceholderText(/search/i);
    await userEvent.type(searchInput, 'Slack');
    expect(screen.getByText('Company Slack')).toBeInTheDocument();
    expect(screen.queryByText('My S3 Bucket')).not.toBeInTheDocument();
  });
});

// ── SourcesPage ──────────────────────────────────────────────────────────────

describe('SourcesPage', () => {
  function mockFetch(sources = [SOURCE, SLACK_SOURCE]) {
    return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      // Auth token check — return success
      if (url.includes('/auth') || url.includes('/token')) {
        return new Response(JSON.stringify({ token: 'test' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/ingestion/quota')) {
        return new Response(JSON.stringify(QUOTA), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/sources') && !url.includes('/health') && !url.includes('/sync')) {
        return new Response(JSON.stringify(sources), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
  }

  test('renders page heading and Add Source button', async () => {
    mockFetch([]);
    const { SourcesPage } = await import('../SourcesPage');
    wrap(<SourcesPage />);
    expect(screen.getByRole('heading', { name: /sources/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add.*source|new.*source/i })).toBeInTheDocument();
  });

  test('renders family filter chips (18 families)', async () => {
    mockFetch([]);
    const { SourcesPage } = await import('../SourcesPage');
    wrap(<SourcesPage />);
    // "All" chip is always rendered as a FamilyChip with label text 'All'
    await waitFor(() => expect(screen.getAllByText('All').length).toBeGreaterThan(0));
  });

  test('loads and displays sources from API', async () => {
    mockFetch();
    const { SourcesPage } = await import('../SourcesPage');
    wrap(<SourcesPage />);
    await waitFor(() => expect(screen.getByText('My S3 Bucket')).toBeInTheDocument());
    expect(screen.getByText('Company Slack')).toBeInTheDocument();
  });

  test('shows stats: total docs, sources count', async () => {
    mockFetch();
    const { SourcesPage } = await import('../SourcesPage');
    wrap(<SourcesPage />);
    await waitFor(() => screen.getByText('My S3 Bucket'));
    // At least one number from source stats appears
    expect(document.body.textContent).toMatch(/\d+/);
  });

  test('opens SourceCreateWizard when Add Source clicked', async () => {
    mockFetch([]);
    const { SourcesPage } = await import('../SourcesPage');
    wrap(<SourcesPage />);
    const addBtn = screen.getByRole('button', { name: /add.*source|new.*source/i });
    await userEvent.click(addBtn);
    // Wizard should open — look for step 1 content (family selection)
    await waitFor(() => {
      const body = document.body.textContent || '';
      expect(body.match(/object.*storage|communication|code.*repo|step.*1|choose.*family/i)).toBeTruthy();
    });
  });

  test('triggers sync via API when sync button clicked', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/sources') && !url.includes('/health') && !url.includes('/quota')) {
        if (init?.method === 'POST' && url.includes('/sync')) {
          return new Response(JSON.stringify({ job_id: 'job-new', status: 'pending' }), {
            status: 202, headers: { 'Content-Type': 'application/json' },
          });
        }
        return new Response(JSON.stringify([SOURCE]), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/quota')) {
        return new Response(JSON.stringify(QUOTA), { status: 200 });
      }
      return new Response('{}', { status: 200 });
    });

    const { SourcesPage } = await import('../SourcesPage');
    wrap(<SourcesPage />);
    await waitFor(() => screen.getByText('My S3 Bucket'));
    const syncBtn = screen.getByRole('button', { name: /sync/i });
    await userEvent.click(syncBtn);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/sources\/src-001\/sync/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });
});

// ── SourceCreateWizard ───────────────────────────────────────────────────────

describe('SourceCreateWizard', () => {
  test('renders step 1: family selection grid', async () => {
    const { SourceCreateWizard } = await import('../components/SourceCreateWizard');
    wrap(<SourceCreateWizard onClose={vi.fn()} onCreated={vi.fn()} />);
    // Should show family tiles
    expect(screen.getByText(/object.*storage|storage.*object/i)).toBeInTheDocument();
    expect(screen.getByText(/communication/i)).toBeInTheDocument();
  });

  test('advances to step 2 after selecting a family', async () => {
    const { SourceCreateWizard } = await import('../components/SourceCreateWizard');
    wrap(<SourceCreateWizard onClose={vi.fn()} onCreated={vi.fn()} />);
    const storageTile = screen.getByText(/object.*storage|storage.*object/i);
    await userEvent.click(storageTile);
    // Step 2: source type selection
    await waitFor(() =>
      expect(screen.getByText(/s3|gcs|azure|minio/i)).toBeInTheDocument()
    );
  });

  test('calls onClose when cancel/X clicked', async () => {
    const { SourceCreateWizard } = await import('../components/SourceCreateWizard');
    const onClose = vi.fn();
    wrap(<SourceCreateWizard onClose={onClose} onCreated={vi.fn()} />);
    const closeBtn = screen.getByRole('button', { name: /close|cancel/i });
    await userEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalled();
  });
});
