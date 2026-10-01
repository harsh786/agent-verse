/**
 * MULTI-INSTANCE-UI: the tool step lists each registered connector INSTANCE
 * separately (by its own name, type as a secondary label) and saves that
 * instance's server id plus a tool it exposes — two MongoDBs are two choices.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { WorkflowStepConfig } from './WorkflowStepConfig';

const ORDERS = { server_id: 'builtin-mongodb:orders-db', name: 'orders-db', connector_type: 'mongodb', url: 'builtin://' };
const ANALYTICS = { server_id: 'builtin-mongodb:analytics-db', name: 'analytics-db', connector_type: 'mongodb', url: 'builtin://' };

function mockBackend() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith(`/connectors/${encodeURIComponent(ANALYTICS.server_id)}/tools`))
      return json([{ name: 'mongodb_find', description: 'Find documents' }, { name: 'mongodb_aggregate' }]);
    if (url.endsWith(`/connectors/${encodeURIComponent(ORDERS.server_id)}/tools`))
      return json([{ name: 'mongodb_find' }]);
    if (url.endsWith('/connectors')) return json([ORDERS, ANALYTICS]);
    return json({});
  });
}

function Harness({ initial, onUpdate }: { initial: Record<string, unknown>; onUpdate: (u: Record<string, unknown>) => void }) {
  const [data, setData] = useState<Record<string, unknown>>({ stepType: 'tool', ...initial });
  const node = { id: 'step-1', type: 'tool', position: { x: 0, y: 0 }, data } as unknown as Node;
  return (
    <WorkflowStepConfig
      node={node}
      onUpdate={(u) => {
        onUpdate(u as Record<string, unknown>);
        setData((d) => ({ ...d, ...(u as Record<string, unknown>) }));
      }}
      onClose={() => undefined}
    />
  );
}

function renderTool(initial: Record<string, unknown> = {}) {
  const onUpdate = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Harness initial={initial} onUpdate={onUpdate} />
    </QueryClientProvider>,
  );
  return onUpdate;
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('WorkflowStepConfig — tool step connector picker (MULTI-INSTANCE-UI)', () => {
  test('lists same-type instances separately and saves the chosen instance id and tool', async () => {
    mockBackend();
    const onUpdate = renderTool();
    const select = await screen.findByLabelText('Connector');
    await waitFor(() => expect(within(select).getAllByRole('option').length).toBe(3));
    const labels = within(select).getAllByRole('option').map((o) => o.textContent);
    expect(labels).toEqual([
      'Any connector (match by tool name)',
      'analytics-db · mongodb',
      'orders-db · mongodb',
    ]);
    fireEvent.change(select, { target: { value: ANALYTICS.server_id } });
    expect(onUpdate).toHaveBeenLastCalledWith({ server_id: ANALYTICS.server_id, tool: undefined });
    expect(screen.getByText(`Server ID: ${ANALYTICS.server_id}`)).toBeInTheDocument();

    const tool = await screen.findByLabelText('Tool');
    await waitFor(() => expect(within(tool).getAllByRole('option').map((o) => o.textContent)).toContain('mongodb_aggregate'));
    fireEvent.change(tool, { target: { value: 'mongodb_aggregate' } });
    expect(onUpdate).toHaveBeenLastCalledWith({ tool: 'mongodb_aggregate' });
  });

  test('an instance id that is no longer registered is shown as missing', async () => {
    mockBackend();
    renderTool({ server_id: 'builtin-mongodb:gone', tool: 'mongodb_find' });
    const select = await screen.findByLabelText('Connector');
    await waitFor(() =>
      expect(within(select).getAllByRole('option').map((o) => o.textContent)).toContain(
        'builtin-mongodb:gone (missing)',
      ),
    );
    expect((select as HTMLSelectElement).value).toBe('builtin-mongodb:gone');
    // The tool stays editable as text since the instance's tools can't be listed.
    expect(screen.getByLabelText('Tool name')).toHaveValue('mongodb_find');
  });

  test('without a connector the tool name stays a free-text field', async () => {
    mockBackend();
    const onUpdate = renderTool();
    await screen.findByLabelText('Connector');
    fireEvent.change(screen.getByLabelText('Tool name'), { target: { value: 'github.create_issue' } });
    expect(onUpdate).toHaveBeenLastCalledWith({ tool: 'github.create_issue' });
  });
});
