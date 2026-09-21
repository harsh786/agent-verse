import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CreateMissionDrawer } from './CreateMissionDrawer';

// AnimatePresence gates the drawer; stub it so exit animations don't linger.
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

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/missions/execute') && method === 'POST')
      return new Response(JSON.stringify({ mission_id: 'm-new', title: 'Research trends', status: 'active', goal_id: 'g1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/attachments') && method === 'POST')
      return new Response(JSON.stringify({ attachment_id: 'a1', path: '/files/a1', filename: 'note.txt', content_type: 'text/plain', size: 12 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function makeFile(name: string, sizeBytes = 1024, type = 'text/plain') {
  const file = new File([new Uint8Array(sizeBytes)], name, { type });
  return file;
}

function renderDrawer(props: Partial<React.ComponentProps<typeof CreateMissionDrawer>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CreateMissionDrawer orgId="o1" open={props.open ?? true} onClose={props.onClose ?? vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CreateMissionDrawer', () => {
  test('renders nothing when closed', () => {
    mockFetch();
    renderDrawer({ open: false });
    expect(screen.queryByRole('heading', { name: /New Mission/i })).not.toBeInTheDocument();
  });

  test('renders the form fields when open', () => {
    mockFetch();
    renderDrawer({ open: true });
    expect(screen.getByRole('heading', { name: /New Mission/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Mission title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Objective/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Priority/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Autonomy level/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Create mission/i })).toBeInTheDocument();
  });

  test('submitting empty shows a validation error and does not POST', async () => {
    const spy = mockFetch();
    renderDrawer({ open: true });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));
    expect(await screen.findByText(/Title is required/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);
  });

  test('a valid submit POSTs to /missions/execute and closes the drawer', async () => {
    const spy = mockFetch();
    const onClose = vi.fn();
    renderDrawer({ open: true, onClose });
    fireEvent.change(screen.getByLabelText(/Mission title/i), { target: { value: 'Research trends' } });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          if (!String(u).includes('/missions/execute') || (i as RequestInit)?.method !== 'POST') return false;
          return JSON.parse(String((i as RequestInit).body)).title === 'Research trends';
        }),
      ).toBe(true),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  test('the close button invokes onClose', () => {
    mockFetch();
    const onClose = vi.fn();
    renderDrawer({ open: true, onClose });
    fireEvent.click(screen.getByRole('button', { name: /Close drawer/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('clicking the backdrop invokes onClose', () => {
    mockFetch();
    const onClose = vi.fn();
    const { container } = renderDrawer({ open: true, onClose });
    const backdrop = container.querySelector('[aria-hidden="true"].fixed.inset-0');
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop as Element);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('pressing Escape invokes onClose', () => {
    mockFetch();
    const onClose = vi.fn();
    renderDrawer({ open: true, onClose });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('pressing a non-Escape key does not invoke onClose', () => {
    mockFetch();
    const onClose = vi.fn();
    renderDrawer({ open: true, onClose });
    fireEvent.keyDown(document, { key: 'Enter' });
    expect(onClose).not.toHaveBeenCalled();
  });

  test('a title shorter than 3 characters shows a minLength error', async () => {
    mockFetch();
    renderDrawer({ open: true });
    fireEvent.change(screen.getByLabelText(/Mission title/i), { target: { value: 'ab' } });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));
    expect(await screen.findByText(/At least 3 characters/i)).toBeInTheDocument();
  });

  test('adding an attachment lists its name and size, and it can be removed', () => {
    mockFetch();
    renderDrawer({ open: true });
    const input = screen.getByLabelText(/Add attachments/i);
    const file = makeFile('report.txt', 2048);
    fireEvent.change(input, { target: { files: [file] } });

    expect(screen.getByText('report.txt')).toBeInTheDocument();
    expect(screen.getByText('2 KB')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Remove report.txt/i }));
    expect(screen.queryByText('report.txt')).not.toBeInTheDocument();
  });

  test('attachments are capped at 5 files', () => {
    mockFetch();
    renderDrawer({ open: true });
    const input = screen.getByLabelText(/Add attachments/i);
    const firstBatch = Array.from({ length: 5 }, (_, i) => makeFile(`f${i}.txt`));
    fireEvent.change(input, { target: { files: firstBatch } });
    for (let i = 0; i < 5; i++) expect(screen.getByText(`f${i}.txt`)).toBeInTheDocument();

    fireEvent.change(input, { target: { files: [makeFile('extra.txt')] } });
    expect(screen.queryByText('extra.txt')).not.toBeInTheDocument();
    expect(screen.getByText('f0.txt')).toBeInTheDocument();
  });

  test('adding a new attachment clears a previous upload error', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/attachments') && method === 'POST')
        return new Response(JSON.stringify({ error: { message: 'Upload rejected' } }), { status: 400, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderDrawer({ open: true });
    fireEvent.change(screen.getByLabelText(/Mission title/i), { target: { value: 'Research trends' } });
    fireEvent.change(screen.getByLabelText(/Add attachments/i), { target: { files: [makeFile('bad.txt')] } });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));

    expect(await screen.findByText(/Upload rejected/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u, i]) => String(u).includes('/missions/execute') && (i as RequestInit)?.method === 'POST')).toBe(false);

    fireEvent.change(screen.getByLabelText(/Add attachments/i), { target: { files: [makeFile('good.txt')] } });
    expect(screen.queryByText(/Upload rejected/i)).not.toBeInTheDocument();
  });

  test('a failed attachment upload shows the fallback message when the error has no message', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/attachments') && method === 'POST') throw 'boom';
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderDrawer({ open: true });
    fireEvent.change(screen.getByLabelText(/Mission title/i), { target: { value: 'Research trends' } });
    fireEvent.change(screen.getByLabelText(/Add attachments/i), { target: { files: [makeFile('bad.txt')] } });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));

    expect(await screen.findByText(/Could not upload attachment\./i)).toBeInTheDocument();
  });

  test('submitting with attachments shows the "Uploading & launching…" label while busy', async () => {
    let resolveUpload!: (r: Response) => void;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/attachments') && method === 'POST')
        return new Promise<Response>((resolve) => { resolveUpload = resolve; });
      if (url.includes('/missions/execute') && method === 'POST')
        return new Response(JSON.stringify({ mission_id: 'm-new', title: 'Research trends', status: 'active', goal_id: 'g1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderDrawer({ open: true });
    fireEvent.change(screen.getByLabelText(/Mission title/i), { target: { value: 'Research trends' } });
    fireEvent.change(screen.getByLabelText(/Add attachments/i), { target: { files: [makeFile('report.txt')] } });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));

    expect(await screen.findByText(/Uploading & launching…/i)).toBeInTheDocument();

    resolveUpload(new Response(JSON.stringify({ attachment_id: 'a1', path: '/files/a1', filename: 'report.txt', content_type: 'text/plain', size: 12 }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

    await waitFor(() => expect(screen.queryByText(/Uploading & launching…/i)).not.toBeInTheDocument());
  });

  test('a failed mission create (after a successful upload) shows the server error message', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/attachments') && method === 'POST')
        return new Response(JSON.stringify({ attachment_id: 'a1', path: '/files/a1', filename: 'report.txt', content_type: 'text/plain', size: 12 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/missions/execute') && method === 'POST')
        return new Response(JSON.stringify({ error: { message: 'Mission service unavailable' } }), { status: 400, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const onClose = vi.fn();
    renderDrawer({ open: true, onClose });
    fireEvent.change(screen.getByLabelText(/Mission title/i), { target: { value: 'Research trends' } });
    fireEvent.change(screen.getByLabelText(/Add attachments/i), { target: { files: [makeFile('report.txt')] } });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));

    expect(await screen.findByText(/Mission service unavailable/i)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
