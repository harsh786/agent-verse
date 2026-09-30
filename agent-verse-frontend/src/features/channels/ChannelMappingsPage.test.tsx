import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ChannelMappingsPage } from './ChannelMappingsPage';

const MAPPINGS = [
  { id: 'm1', channel_type: 'slack', channel_id: 'T12345ABCD', status: 'verified', created_at: '2026-01-01T00:00:00Z' },
  { id: 'm2', channel_type: 'email', channel_id: 'support@company.com', status: 'legacy_unverified' },
  { id: 'm4', channel_type: 'discord', channel_id: 'G-777', status: 'pending_verification' },
  { id: 'm5', channel_type: 'slack', channel_id: 'T-LEGACY', status: 'legacy_unverified' },
];

const ISSUED = {
  id: 'm3',
  channel_type: 'slack',
  channel_id: 'T99999ZZZZ',
  status: 'pending_verification',
  verification_code: 'AV-ABCD2345',
  verification_expires_at: '2026-10-01T12:00:00+00:00',
  instructions: 'Send the code AV-ABCD2345 as a message on this slack channel.',
};

interface MockOpts { mappings?: unknown[]; listError?: boolean; postStatus?: number; postBody?: unknown }

function mockFetch(opts: MockOpts = {}) {
  const { mappings = MAPPINGS, listError = false, postStatus = 200, postBody = ISSUED } = opts;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/verify') && method === 'POST') {
      return json({ ...ISSUED, id: 'm5', channel_type: 'slack', status: 'legacy_unverified', verification_code: 'AV-WXYZ6789' });
    }
    if (url.includes('/channels/mappings') && method === 'POST') return json(postBody, postStatus);
    if (url.includes('/channels/mappings')) {
      if (listError) return new Response('boom', { status: 500 });
      return json(mappings);
    }
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ChannelMappingsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ChannelMappingsPage', () => {
  test('renders the heading and Add Channel action', async () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: /Channel Connections/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add Channel/i })).toBeInTheDocument();
  });

  test('lists channel mappings from the API', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByText('T12345ABCD')).toBeInTheDocument();
    expect(screen.getByText('support@company.com')).toBeInTheDocument();
  });

  // TRG-03: channel ownership is proven from the channel itself.
  test('shows each mapping\'s verification status and a Verify action for unproven ones', async () => {
    mockFetch();
    renderPage();
    const slackRow = (await screen.findByText('T12345ABCD')).closest('[data-testid="channel-row"]') as HTMLElement;
    const emailRow = screen.getByText('support@company.com').closest('[data-testid="channel-row"]') as HTMLElement;
    const discordRow = screen.getByText('G-777').closest('[data-testid="channel-row"]') as HTMLElement;

    expect(within(slackRow).getByText(/^Verified$/)).toBeInTheDocument();
    expect(within(slackRow).queryByRole('button', { name: /Verify/i })).not.toBeInTheDocument();
    const legacySlackRow = screen.getByText('T-LEGACY').closest('[data-testid="channel-row"]') as HTMLElement;
    expect(within(legacySlackRow).getByText(/Unverified/i)).toBeInTheDocument();
    expect(within(legacySlackRow).getByRole('button', { name: /Verify/i })).toBeInTheDocument();
    // A legacy email mapping cannot be verified by code: it is under operator review.
    expect(within(emailRow).getByText(/Under operator review/i)).toBeInTheDocument();
    expect(within(emailRow).queryByRole('button', { name: /Verify/i })).not.toBeInTheDocument();
    expect(within(discordRow).getByText(/Pending verification/i)).toBeInTheDocument();
    expect(within(discordRow).getByRole('button', { name: /Verify/i })).toBeInTheDocument();
  });

  // Owner decision: a code only proves someone can SEND to a number/address.
  test('operator-approval, superseded and rejected mappings show their status and no Verify', async () => {
    mockFetch({
      mappings: [
        { id: 'a', channel_type: 'sms', channel_id: '+15550100', status: 'pending_operator_approval' },
        { id: 'b', channel_type: 'slack', channel_id: 'T-OLD', status: 'superseded' },
        { id: 'c', channel_type: 'email', channel_id: 'x@victim.test', status: 'rejected' },
      ],
    });
    renderPage();
    const row = async (id: string) =>
      (await screen.findByText(id)).closest('[data-testid="channel-row"]') as HTMLElement;
    const sms = await row('+15550100');
    expect(within(sms).getByText(/Awaiting operator approval/i)).toBeInTheDocument();
    const old = await row('T-OLD');
    expect(within(old).getByText(/Superseded/i)).toBeInTheDocument();
    const rejected = await row('x@victim.test');
    expect(within(rejected).getByText(/Rejected/i)).toBeInTheDocument();
    for (const r of [sms, old, rejected]) {
      expect(within(r).queryByRole('button', { name: /Verify/i })).not.toBeInTheDocument();
    }
  });

  test('connecting an SMS number says it is awaiting operator approval (no code)', async () => {
    mockFetch({
      mappings: [],
      postBody: {
        id: 'm9', channel_type: 'sms', channel_id: '+15550100', status: 'pending_operator_approval',
        verification_code: null, verification_expires_at: null,
      },
    });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await userEvent.selectOptions(screen.getByRole('combobox'), 'sms');
    await userEvent.type(screen.getByPlaceholderText('+15551234567'), '+15550100');
    await userEvent.click(screen.getByRole('button', { name: /Connect Channel/i }));
    const panel = await screen.findByRole('region', { name: /Awaiting operator approval/i });
    expect(within(panel).getByText(/only proves someone can send/i)).toBeInTheDocument();
    expect(within(panel).getByText(/not routed until/i)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: /Verify channel ownership/i })).not.toBeInTheDocument();
  });

  test('connecting a channel shows its one-time verification code', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await userEvent.type(screen.getByPlaceholderText('T12345ABCD'), 'T99999ZZZZ');
    await userEvent.click(screen.getByRole('button', { name: /Connect Channel/i }));
    const panel = await screen.findByRole('region', { name: /Verify channel ownership/i });
    expect(within(panel).getByText('AV-ABCD2345')).toBeInTheDocument();
    expect(within(panel).getByText(/not routed until/i)).toBeInTheDocument();
  });

  test('Verify on a legacy mapping requests a fresh code for that mapping', async () => {
    const spy = mockFetch();
    renderPage();
    const legacyRow = (await screen.findByText('T-LEGACY')).closest('[data-testid="channel-row"]') as HTMLElement;
    await userEvent.click(within(legacyRow).getByRole('button', { name: /Verify/i }));
    const panel = await screen.findByRole('region', { name: /Verify channel ownership/i });
    expect(within(panel).getByText('AV-WXYZ6789')).toBeInTheDocument();
    expect(within(panel).getByText(/keeps routing/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([u, i]) =>
      String(u).includes('/channels/mappings/m5/verify') && (i as RequestInit | undefined)?.method === 'POST',
    )).toBe(true);
  });

  test('a channel already verified by another tenant shows an already-claimed message', async () => {
    mockFetch({ mappings: [], postStatus: 409, postBody: { detail: 'Channel is already verified by another tenant' } });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await userEvent.type(screen.getByPlaceholderText('T12345ABCD'), 'T12345ABCD');
    await userEvent.click(screen.getByRole('button', { name: /Connect Channel/i }));
    expect(await screen.findByText(/already claimed by another organization/i)).toBeInTheDocument();
  });

  test('shows the empty state when there are no mappings', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    expect(await screen.findByText(/No channels connected/i)).toBeInTheDocument();
  });

  test('shows the error state and Retry when the load fails', async () => {
    mockFetch({ listError: true });
    renderPage();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Failed to load channel mappings/i);
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
  });

  test('opening the modal and connecting a channel POSTs to /channels/mappings', async () => {
    const spy = mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    expect(await screen.findByRole('dialog', { name: /Add channel connection/i })).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText('T12345ABCD'), 'T99999ZZZZ');
    await userEvent.click(screen.getByRole('button', { name: /Connect Channel/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        const init = i as RequestInit | undefined;
        return String(u).includes('/channels/mappings') &&
          init?.method === 'POST' &&
          String(init?.body ?? '').includes('T99999ZZZZ');
      })).toBe(true),
    );
  });

  test('Connect Channel is disabled until an id is entered', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    expect(await screen.findByRole('button', { name: /Connect Channel/i })).toBeDisabled();
  });

  test('shows loading skeletons before the mappings resolve', async () => {
    mockFetch();
    const { container } = renderPage();
    expect(container.querySelectorAll('.animate-pulse').length).toBe(2);
    await waitFor(() => expect(container.querySelectorAll('.animate-pulse').length).toBe(0));
  });

  test('shows a fallback icon for an unmapped channel type', async () => {
    mockFetch({ mappings: [{ id: 'm9', channel_type: 'webhook', channel_id: 'wh-1' }] });
    renderPage();
    expect(await screen.findByText('wh-1')).toBeInTheDocument();
    expect(screen.getByLabelText('webhook')).toHaveTextContent('🔗');
  });

  test('clicking Retry re-triggers the mappings fetch', async () => {
    const spy = mockFetch({ listError: true });
    renderPage();
    const alert = await screen.findByRole('alert');
    const callsBefore = spy.mock.calls.length;
    await userEvent.click(within(alert).getByRole('button', { name: /Retry/i }));
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  test('clicking the backdrop closes the add channel modal', async () => {
    mockFetch({ mappings: [] });
    const { container } = renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await screen.findByRole('dialog', { name: /Add channel connection/i });
    const backdrop = container.querySelector('.absolute.inset-0.bg-black\\/40');
    expect(backdrop).not.toBeNull();
    await userEvent.click(backdrop as HTMLElement);
    expect(screen.queryByRole('dialog', { name: /Add channel connection/i })).not.toBeInTheDocument();
  });

  test('clicking Cancel closes the add channel modal', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await screen.findByRole('dialog', { name: /Add channel connection/i });
    await userEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(screen.queryByRole('dialog', { name: /Add channel connection/i })).not.toBeInTheDocument();
  });

  test('changing channel type to a non-slack/email type updates label and placeholder', async () => {
    mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    const dialog = await screen.findByRole('dialog', { name: /Add channel connection/i });
    const select = within(dialog).getByRole('combobox');

    await userEvent.selectOptions(select, 'email');
    expect(within(dialog).getByText('Email Address')).toBeInTheDocument();
    expect(within(dialog).getByPlaceholderText('support@company.com')).toBeInTheDocument();

    await userEvent.selectOptions(select, 'discord');
    expect(within(dialog).getByText('Channel ID')).toBeInTheDocument();
    expect(within(dialog).getByPlaceholderText('channel-id')).toBeInTheDocument();
  });

  // TRG-01: Teams routes by Microsoft 365 tenant ID, never the shared serviceUrl.
  test('Teams asks for the Microsoft 365 tenant ID and validates it as a GUID', async () => {
    const spy = mockFetch({ mappings: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    const dialog = await screen.findByRole('dialog', { name: /Add channel connection/i });
    await userEvent.selectOptions(within(dialog).getByRole('combobox'), 'teams');
    expect(within(dialog).getByText('Microsoft 365 tenant ID')).toBeInTheDocument();

    const input = within(dialog).getByRole('textbox');
    await userEvent.type(input, 'https://smba.trafficmanager.net/amer/');
    expect(within(dialog).getByText(/must be a GUID/i)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /Connect Channel/i })).toBeDisabled();

    await userEvent.clear(input);
    await userEvent.type(input, '72f988bf-86f1-41af-91ab-2d7cd011db47');
    expect(within(dialog).queryByText(/must be a GUID/i)).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /Connect Channel/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        const init = i as RequestInit | undefined;
        return String(u).includes('/channels/mappings') && init?.method === 'POST' &&
          String(init?.body ?? '').includes('72f988bf-86f1-41af-91ab-2d7cd011db47');
      })).toBe(true),
    );
  });

  test('flags a legacy Teams serviceUrl mapping as needing re-mapping', async () => {
    mockFetch({
      mappings: [
        { id: 'm1', channel_type: 'teams', channel_id: 'https://smba.trafficmanager.net/amer/', needs_remapping: true },
      ],
    });
    renderPage();
    expect(await screen.findByText(/Needs re-mapping/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Connected$/)).not.toBeInTheDocument();
  });

  test('shows a connecting state while the create mutation is pending', async () => {
    let resolvePost: (value: Response) => void = () => {};
    const postPromise = new Promise<Response>((resolve) => {
      resolvePost = resolve;
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
      if (url.includes('/channels/mappings') && method === 'POST') return postPromise;
      if (url.includes('/channels/mappings')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /Add Channel/i }));
    await userEvent.type(screen.getByPlaceholderText('T12345ABCD'), 'T99999ZZZZ');
    await userEvent.click(screen.getByRole('button', { name: /Connect Channel/i }));
    expect(await screen.findByRole('button', { name: /Connecting/i })).toBeInTheDocument();

    resolvePost(new Response(JSON.stringify(ISSUED), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Add channel connection/i })).not.toBeInTheDocument());
  });
});
