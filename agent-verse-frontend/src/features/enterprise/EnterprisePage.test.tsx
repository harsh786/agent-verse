import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { EnterprisePage } from './EnterprisePage';

function renderEnterprisePage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <EnterprisePage />
    </QueryClientProvider>
  );
}

describe('EnterprisePage', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'enterprise',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders Export, Residency, and Delete sections', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ region: 'us-east-1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderEnterprisePage();
    expect(screen.getByText('Export My Data')).toBeInTheDocument();
    expect(screen.getByText('Data Residency')).toBeInTheDocument();
    expect(screen.getByText('Delete My Data')).toBeInTheDocument();
  });

  test('Export button calls the compliance export API', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input) => {
        const url = String(input);
        if (url.includes('/enterprise/compliance/export')) {
          return new Response(
            JSON.stringify({ message: 'Export completed.' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(
          JSON.stringify({ region: 'us-east-1' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
    );

    renderEnterprisePage();
    await userEvent.click(screen.getByRole('button', { name: /^export$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/enterprise\/compliance\/export$/),
        expect.anything()
      )
    );
  });

  test('shows export result message on successful export', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/enterprise/compliance/export')) {
        return new Response(
          JSON.stringify({ message: 'Export completed successfully.' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        JSON.stringify({ region: 'us-east-1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderEnterprisePage();
    await userEvent.click(screen.getByRole('button', { name: /^export$/i }));
    await waitFor(() =>
      expect(screen.getByText('Export completed successfully.')).toBeInTheDocument()
    );
  });

  test('download button fetches the export as an authenticated blob, not a bare anchor href', async () => {
    // Regression: the download_url the backend returns is a bare relative
    // path (e.g. "/enterprise/compliance/export/{id}/download") meant for
    // the API origin, and its endpoint requires the same auth header every
    // other API call attaches. A plain `<a href={download_url}>` resolved
    // against the frontend's own origin (wrong) and could never send that
    // header (a browser anchor can't) even if the origin were fixed. The
    // fix routes the download through `downloadAuthenticated` (which
    // attaches the API-key/Bearer header and hits API_BASE_URL) followed by
    // `triggerBlobDownload` (blob URL + programmatic anchor), rendered as a
    // <button>, not a navigable <a>.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/enterprise/compliance/export/') && url.includes('/download')) {
        return new Response(new Blob(['{"exported":true}'], { type: 'application/json' }), {
          status: 200,
        });
      }
      if (url.includes('/enterprise/compliance/export')) {
        return new Response(
          JSON.stringify({
            download_url: '/enterprise/compliance/export/req-123/download',
            expires_at: '2026-12-31T00:00:00Z',
            size_bytes: 1024 * 1024,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        JSON.stringify({ region: 'eu-west-1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    if (!URL.createObjectURL) (URL as unknown as { createObjectURL: unknown }).createObjectURL = () => 'blob:mock';
    if (!URL.revokeObjectURL) (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = () => {};
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const createUrlSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    const revokeUrlSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    renderEnterprisePage();
    await userEvent.click(screen.getByRole('button', { name: /^export$/i }));
    await waitFor(() => expect(screen.getByText('Export ready')).toBeInTheDocument());

    const downloadButton = screen.getByRole('button', { name: /download export/i });
    await userEvent.click(downloadButton);

    await waitFor(() => expect(createUrlSpy).toHaveBeenCalledTimes(1));
    // The download itself must resolve against the API origin, not the
    // frontend's own origin, and must carry an auth header.
    const downloadCall = fetchSpy.mock.calls.find(([input]) =>
      String(input).includes('/enterprise/compliance/export/req-123/download')
    );
    expect(downloadCall).toBeDefined();
    const [calledUrl, calledInit] = downloadCall as [unknown, RequestInit | undefined];
    expect(String(calledUrl)).toMatch(/^https?:\/\//);
    const headers = calledInit?.headers as Record<string, string> | undefined;
    expect(headers?.['X-API-Key'] || headers?.['Authorization']).toBeTruthy();
    expect(clickSpy).toHaveBeenCalledTimes(1);

    clickSpy.mockRestore();
    createUrlSpy.mockRestore();
    revokeUrlSpy.mockRestore();
  });

  test('Delete button shows confirmation form when clicked', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ region: 'us-east-1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderEnterprisePage();
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
    expect(screen.getByPlaceholderText('DELETE MY DATA')).toBeInTheDocument();
  });

  test('Confirm Delete button is disabled until "DELETE MY DATA" is typed', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ region: 'us-east-1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderEnterprisePage();
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));

    const confirmBtn = screen.getByRole('button', { name: /confirm delete/i });
    expect(confirmBtn).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText('DELETE MY DATA'), 'DELETE MY');
    expect(confirmBtn).toBeDisabled();

    await userEvent.type(screen.getByPlaceholderText('DELETE MY DATA'), ' DATA');
    expect(confirmBtn).not.toBeDisabled();
  });

  test('Residency section shows region information from API', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/enterprise/compliance/residency')) {
        return new Response(
          JSON.stringify({
            region: 'eu-west-1',
            data_center: 'Frankfurt',
            compliance_frameworks: ['GDPR', 'ISO27001'],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(null, { status: 404 });
    });

    renderEnterprisePage();
    await waitFor(() => expect(screen.getByText('eu-west-1')).toBeInTheDocument());
    expect(screen.getByText('Frankfurt')).toBeInTheDocument();
    expect(screen.getByText('GDPR')).toBeInTheDocument();
    expect(screen.getByText('ISO27001')).toBeInTheDocument();
  });

  test('shows data deletion scheduled message after successful delete', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/enterprise/purge') && init?.method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      return new Response(
        JSON.stringify({ region: 'us-east-1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderEnterprisePage();
    await userEvent.click(screen.getByRole('button', { name: /^delete$/i }));
    const input = screen.getByPlaceholderText('DELETE MY DATA');
    await userEvent.type(input, 'DELETE MY DATA');
    await userEvent.click(screen.getByRole('button', { name: /confirm delete/i }));

    await waitFor(() =>
      expect(screen.getByText(/data deletion scheduled/i)).toBeInTheDocument()
    );
  });
});
