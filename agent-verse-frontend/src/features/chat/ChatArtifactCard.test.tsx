/** Phase 7 — downloadable artifact card from an artifact_created event. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatArtifactCard } from './ChatArtifactCard';
import { API_BASE } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';

// jsdom has no object-URL support; install spies for the blob download path.
const originalCreate = URL.createObjectURL;
const originalRevoke = URL.revokeObjectURL;
let createObjectURL: ReturnType<typeof vi.fn>;
let revokeObjectURL: ReturnType<typeof vi.fn>;

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'secret-key', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
  useToastStore.setState({ toasts: [] });
  createObjectURL = vi.fn(() => 'blob:http://test/abc');
  revokeObjectURL = vi.fn();
  URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = revokeObjectURL as unknown as typeof URL.revokeObjectURL;
});
afterEach(() => {
  URL.createObjectURL = originalCreate;
  URL.revokeObjectURL = originalRevoke;
  vi.restoreAllMocks();
});

describe('ChatArtifactCard', () => {
  const artifact = { artifactId: 'art_1', title: 'report.pdf', language: 'pdf' };

  it('renders the download control as a button, never a link carrying the api key in its URL', () => {
    const { container } = render(<ChatArtifactCard artifact={artifact} />);
    expect(screen.getByRole('button', { name: /download report\.pdf/i })).toBeDefined();
    expect(screen.queryByRole('link', { name: /download report\.pdf/i })).toBeNull();
    for (const el of container.querySelectorAll('[href]')) {
      expect(el.getAttribute('href')).not.toContain('api_key=');
    }
  });

  it('downloads via an authenticated fetch + blob (key in the header, not the URL)', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('pdf-bytes', { status: 200, headers: { 'Content-Type': 'application/pdf' } }),
    );
    const clicked: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this);
    });

    render(<ChatArtifactCard artifact={artifact} />);
    await userEvent.click(screen.getByRole('button', { name: /download report\.pdf/i }));

    await waitFor(() => expect(clicked).toHaveLength(1));
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE}/chat/artifacts/art_1/download`);
    expect(url).not.toContain('api_key=');
    expect(url).not.toContain('secret-key');
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('secret-key');

    // The fetched body is handed to the browser as an object URL on a
    // transient anchor with the artifact's filename.
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(createObjectURL.mock.calls[0][0]).toBeInstanceOf(Blob);
    expect(clicked[0].getAttribute('href')).toBe('blob:http://test/abc');
    expect(clicked[0].download).toBe('report.pdf');
    expect(clicked[0].isConnected).toBe(false); // removed after the click
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith('blob:http://test/abc'));
  });

  it('toasts an error and triggers no download when the fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('nope', { status: 403 }));
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<ChatArtifactCard artifact={artifact} />);
    await userEvent.click(screen.getByRole('button', { name: /download report\.pdf/i }));

    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message.includes('report.pdf')),
      ).toBe(true),
    );
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
  });

  it('shows the title and language', () => {
    render(<ChatArtifactCard artifact={artifact} />);
    expect(screen.getByText('report.pdf')).toBeDefined();
    expect(screen.getByText('pdf')).toBeDefined();
  });

  it('fires onOpen with the artifact id when the open button is clicked', async () => {
    const onOpen = vi.fn();
    render(<ChatArtifactCard artifact={artifact} onOpen={onOpen} />);
    await userEvent.click(screen.getByRole('button', { name: /open report\.pdf/i }));
    expect(onOpen).toHaveBeenCalledWith('art_1');
  });

  it('omits the open button when no onOpen handler is given', () => {
    render(<ChatArtifactCard artifact={artifact} />);
    expect(screen.queryByRole('button', { name: /open/i })).toBeNull();
  });
});
