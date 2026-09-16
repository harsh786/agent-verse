import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AuditPanel } from './AuditPanel';

const RECORDS = [
  { id: 'r1', sequence: 1, entry_hash: 'abcdef012345deadbeef', action: 'goal.executed', tool_name: 'jira.create', actor: 'agent-1' },
  { id: 'r2', sequence: 2, entry_hash: 'ffeeddccbbaa998877', action: 'tool.invoked', tool_name: 'github.read', actor: 'agent-2' },
];

function mockFetch(opts: { records?: unknown[]; verify?: unknown } = {}) {
  const records = opts.records ?? RECORDS;
  // Real /trust/audit/integrity shape: {status, verified, chain_tip_hash, events_verified, tampered_event, message}
  const verifyDefaults = { verified: true, events_verified: 2, tampered_event: null };
  const verify = { ...verifyDefaults, ...(opts.verify ?? {}) };
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/trust/audit/integrity'))
      return new Response(JSON.stringify(verify), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/trust/audit/export'))
      return new Response(JSON.stringify({
        tenant_id: 't', exported_at: '2026-01-01T00:00:00Z', event_count: records.length,
        events: records, integrity_hash: 'deadbeef', format_version: '1.0',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AuditPanel /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  // jsdom lacks createObjectURL and treats anchor.click() navigation as unimplemented.
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock');
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
});
afterEach(() => vi.restoreAllMocks());

describe('AuditPanel', () => {
  test('renders the three section headings', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('Hash Chain Integrity')).toBeInTheDocument();
    expect(screen.getByText('Export Audit Trail')).toBeInTheDocument();
    expect(screen.getByText('Recent Audit Records')).toBeInTheDocument();
  });

  test('renders recent audit records parsed from the export payload', async () => {
    mockFetch();
    renderPanel();
    expect(await screen.findByText('goal.executed')).toBeInTheDocument();
    expect(screen.getByText('tool.invoked')).toBeInTheDocument();
    expect(screen.getByText('jira.create')).toBeInTheDocument();
    expect(screen.getByText('agent-2')).toBeInTheDocument();
  });

  test('shows the empty state when there are no records', async () => {
    mockFetch({ records: [] });
    renderPanel();
    expect(await screen.findByText(/No audit records yet/i)).toBeInTheDocument();
  });

  test('Verify Chain hits the integrity endpoint and shows an intact result', async () => {
    const spy = mockFetch({ verify: { verified: true, events_verified: 7, tampered_event: null } });
    renderPanel();
    await screen.findByText('goal.executed');
    await userEvent.click(screen.getByRole('button', { name: /Verify Chain/i }));
    expect(await screen.findByText('Chain Intact')).toBeInTheDocument();
    expect(screen.getByText(/7 records verified/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u]) => String(u).includes('/trust/audit/integrity'))).toBe(true);
  });

  test('a tampered chain result surfaces the broken-at marker', async () => {
    mockFetch({ verify: { verified: false, events_verified: 3, tampered_event: 'seq-4' } });
    renderPanel();
    await screen.findByText('goal.executed');
    await userEvent.click(screen.getByRole('button', { name: /Verify Chain/i }));
    expect(await screen.findByText('Chain Tampered!')).toBeInTheDocument();
    expect(screen.getByText(/Broken at: seq-4/i)).toBeInTheDocument();
  });

  test('choosing CSV and downloading requests the export and builds a CSV blob', async () => {
    const spy = mockFetch();
    renderPanel();
    await screen.findByText('goal.executed');
    await userEvent.selectOptions(screen.getByRole('combobox'), 'csv');
    await userEvent.click(screen.getByRole('button', { name: /Download/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => String(u).includes('/trust/audit/export'))).toBe(true),
    );
    expect(globalThis.URL.createObjectURL).toHaveBeenCalled();
  });
});
