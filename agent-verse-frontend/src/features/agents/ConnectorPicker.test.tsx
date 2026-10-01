import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectorPicker } from './ConnectorPicker';

// Two instances of the SAME connector type with different names — each is its
// own registered connector with its own (opaque) server id.
const ORDERS = { server_id: 'builtin-mongodb:orders-db', name: 'orders-db', display_name: 'orders-db', builtin_type: 'builtin-mongodb', builtin_type_name: 'MongoDB', url: 'builtin://', status: 'active' };
const ANALYTICS = { server_id: 'builtin-mongodb:analytics-db', name: 'analytics-db', display_name: 'analytics-db', builtin_type: 'builtin-mongodb', builtin_type_name: 'MongoDB', url: 'builtin://', status: 'active' };
const GITHUB = { server_id: 'c0ffee12', name: 'GitHub (work)', builtin_type: 'builtin-github', builtin_type_name: 'GitHub', url: 'https://api.github.com', status: 'active' };

function mockConnectors(list: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    new Response(JSON.stringify(list), { status, headers: { 'Content-Type': 'application/json' } }),
  );
}

function Harness({ initial = [] as string[], onChange }: { initial?: string[]; onChange?: (ids: string[]) => void }) {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <>
      <ConnectorPicker
        value={value}
        onChange={(ids) => {
          setValue(ids);
          onChange?.(ids);
        }}
      />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}

function renderPicker(props: { initial?: string[]; onChange?: (ids: string[]) => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/agents/new']}>
        <Routes>
          <Route path="/agents/new" element={<Harness {...props} />} />
          <Route path="/connectors/catalog" element={<div>Connector catalog</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const value = () => JSON.parse(screen.getByTestId('value').textContent ?? '[]') as string[];

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ConnectorPicker (UI-AGENT-CONNECTOR-PICKER)', () => {
  test('lists every registered connector by name with its type and server id', async () => {
    mockConnectors([ORDERS, ANALYTICS, GITHUB]);
    renderPicker();
    const orders = await screen.findByRole('checkbox', { name: /orders-db/ });
    const analytics = screen.getByRole('checkbox', { name: /analytics-db/ });
    expect(orders).not.toBeChecked();
    expect(analytics).not.toBeChecked();
    expect(screen.getByText('builtin-mongodb:orders-db')).toBeInTheDocument();
    expect(screen.getByText('builtin-mongodb:analytics-db')).toBeInTheDocument();
    expect(screen.getAllByText('MongoDB')).toHaveLength(2);
    expect(screen.getByText('GitHub')).toBeInTheDocument();
  });

  test('two instances of the same type are selected independently by their own id', async () => {
    const onChange = vi.fn();
    mockConnectors([ORDERS, ANALYTICS]);
    renderPicker({ onChange });
    await userEvent.click(await screen.findByRole('checkbox', { name: /analytics-db/ }));
    expect(onChange).toHaveBeenLastCalledWith(['builtin-mongodb:analytics-db']);
    expect(screen.getByRole('checkbox', { name: /orders-db/ })).not.toBeChecked();
    await userEvent.click(screen.getByRole('checkbox', { name: /orders-db/ }));
    expect(value()).toEqual(['builtin-mongodb:analytics-db', 'builtin-mongodb:orders-db']);
    await userEvent.click(screen.getByRole('checkbox', { name: /analytics-db/ }));
    expect(value()).toEqual(['builtin-mongodb:orders-db']);
  });

  test('shows selected ids that are no longer registered as "missing" and lets them be removed', async () => {
    mockConnectors([ORDERS]);
    renderPicker({ initial: ['builtin-mongodb:orders-db', 'old-jira-123'] });
    expect(await screen.findByRole('checkbox', { name: /orders-db/ })).toBeChecked();
    const missing = screen.getByRole('checkbox', { name: /old-jira-123/ });
    expect(missing).toBeChecked();
    const row = missing.closest('label') as HTMLElement;
    expect(within(row).getByText(/missing/i)).toBeInTheDocument();
    await userEvent.click(missing);
    expect(value()).toEqual(['builtin-mongodb:orders-db']);
    // Once removed it is gone (it isn't a registered connector to re-pick).
    expect(screen.queryByRole('checkbox', { name: /old-jira-123/ })).not.toBeInTheDocument();
  });

  test('filters a long list by name, type or server id', async () => {
    const many = Array.from({ length: 8 }, (_, i) => ({
      server_id: `srv-${i}`, name: `Connector ${i}`, builtin_type: i === 5 ? 'builtin-slack' : null, builtin_type_name: i === 5 ? 'Slack' : '', url: 'x',
    }));
    mockConnectors(many);
    renderPicker();
    await screen.findByRole('checkbox', { name: /Connector 0/ });
    await userEvent.type(screen.getByRole('searchbox', { name: /filter connectors/i }), 'slack');
    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
    expect(screen.getByRole('checkbox', { name: /Connector 5/ })).toBeInTheDocument();
  });

  test('with no connectors registered it offers to add one', async () => {
    mockConnectors([]);
    renderPicker();
    expect(await screen.findByText('No connectors registered yet.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Add a connector' }));
    expect(await screen.findByText('Connector catalog')).toBeInTheDocument();
  });

  test('a failed list load is reported and keeps the selected ids', async () => {
    mockConnectors({ detail: 'registry down' }, 503);
    renderPicker({ initial: ['c0ffee12'] });
    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't load connectors/i);
    expect(screen.getByText('c0ffee12')).toBeInTheDocument();
    expect(value()).toEqual(['c0ffee12']);
  });
});
