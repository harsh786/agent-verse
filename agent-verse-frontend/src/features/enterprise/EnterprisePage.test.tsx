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

  test('shows download link when export returns a download_url', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/enterprise/compliance/export')) {
        return new Response(
          JSON.stringify({
            download_url: 'https://example.com/export.zip',
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

    renderEnterprisePage();
    await userEvent.click(screen.getByRole('button', { name: /^export$/i }));
    await waitFor(() => expect(screen.getByText('Export ready')).toBeInTheDocument());
    expect(screen.getByRole('link', { name: /download export/i })).toBeInTheDocument();
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
      if (url.includes('/enterprise/data-residency')) {
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
