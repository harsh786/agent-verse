import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectorCredentialForm } from './ConnectorCredentialForm';
import type { CatalogConnector } from '../connectorsApi';

// framer-motion stub — avoid animation timing / AnimatePresence in tests.
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

const CONNECTOR: CatalogConnector = {
  name: 'github',
  display_name: 'GitHub',
  description: 'Connect your GitHub account to let agents open PRs.',
  auth_type: 'api_key',
  default_url: 'https://api.github.com',
  icon: 'gh',
  category: 'dev_tools',
  auth_fields: [
    { key: 'token', label: 'Personal Access Token', placeholder: 'ghp_xxx', field_type: 'password', required: true, hint: 'Needs repo scope' },
    { key: 'org', label: 'Organisation', placeholder: 'acme', field_type: 'text', required: false },
  ],
  has_builtin: true,
  builtin_server_id: null,
  is_configured: false,
  connector_type: 'mcp',
};

function mockFetch(testResult: unknown = { server_id: 'srv-1', status: 'passed', detail: 'Authenticated as @octocat', latency_ms: 42 }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (/\/connectors\/[^/]+\/test$/.test(url) && method === 'POST')
      return new Response(JSON.stringify(testResult), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/connectors$/.test(url) && method === 'POST')
      return new Response(JSON.stringify({ server_id: 'srv-1', name: 'github', url: 'https://api.github.com', auth_type: 'api_key', auth_config: {} }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderForm(props: Partial<React.ComponentProps<typeof ConnectorCredentialForm>> = {}) {
  return render(
    <ConnectorCredentialForm
      connector={CONNECTOR}
      onClose={props.onClose ?? vi.fn()}
      onInstalled={props.onInstalled ?? vi.fn()}
    />,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ConnectorCredentialForm', () => {
  test('renders header, description and a field per auth_fields entry', () => {
    mockFetch();
    renderForm();
    expect(screen.getByRole('heading', { name: /Connect GitHub/i })).toBeInTheDocument();
    expect(screen.getByText(/Connect your GitHub account/i)).toBeInTheDocument();
    // Secret field is a password input; non-secret is text.
    const token = screen.getByLabelText(/Personal Access Token/i) as HTMLInputElement;
    expect(token.type).toBe('password');
    expect(screen.getByLabelText(/Organisation/i)).toBeInTheDocument();
    // URL is not among fields → its default seeds nothing visible here.
    expect(screen.getByText(/stored encrypted as vault references/i)).toBeInTheDocument();
  });

  test('submit is disabled until required fields are filled', () => {
    mockFetch();
    renderForm();
    const submit = screen.getByRole('button', { name: /Connect & verify/i });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Personal Access Token/i), { target: { value: 'dummy-token-123' } });
    expect(submit).toBeEnabled();
  });

  test('the close button invokes onClose', () => {
    mockFetch();
    const onClose = vi.fn();
    renderForm({ onClose });
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('submitting registers the connector then tests it, and reports success', async () => {
    const spy = mockFetch();
    const onInstalled = vi.fn();
    renderForm({ onInstalled });
    fireEvent.change(screen.getByLabelText(/Personal Access Token/i), { target: { value: 'dummy-token-123' } });
    fireEvent.click(screen.getByRole('button', { name: /Connect & verify/i }));

    // Register POST /connectors carries the (dummy) secret in auth_config.
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          if (!/\/connectors$/.test(String(u)) || (i as RequestInit)?.method !== 'POST') return false;
          const body = JSON.parse(String((i as RequestInit).body));
          return body.name === 'github' && body.auth_config?.token === 'dummy-token-123';
        }),
      ).toBe(true),
    );
    // Then the credential test fires against the created server id.
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/connectors\/srv-1\/test$/.test(String(u)) && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
    expect(onInstalled).toHaveBeenCalled();
    expect(await screen.findByText(/Authenticated as @octocat/i)).toBeInTheDocument();
  });

  test('a failed credential test surfaces the error detail', async () => {
    mockFetch({ server_id: 'srv-1', status: 'failed', error: 'Bad credentials' });
    renderForm();
    fireEvent.change(screen.getByLabelText(/Personal Access Token/i), { target: { value: 'wrong' } });
    fireEvent.click(screen.getByRole('button', { name: /Connect & verify/i }));
    expect(await screen.findByText(/Bad credentials/i)).toBeInTheDocument();
  });
});
