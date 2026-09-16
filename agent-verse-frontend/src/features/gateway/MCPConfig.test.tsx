import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MCPConfig } from './MCPConfig';

let writeText: ReturnType<typeof vi.fn>;

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });
});
afterEach(() => vi.restoreAllMocks());

describe('MCPConfig', () => {
  test('renders the org-scoped MCP endpoint and the tool list', () => {
    render(<MCPConfig orgId="org-42" />);
    expect(screen.getByRole('heading', { name: /MCP Server/i })).toBeInTheDocument();
    // The endpoint embeds the org id under /v1/mcp/.
    const endpoint = screen.getAllByText(/\/v1\/mcp\/org-42$/)[0];
    expect(endpoint).toBeInTheDocument();
    expect(endpoint.textContent).toMatch(/^wss:\/\//);
    // Every default tool is listed.
    expect(screen.getByText('ask_organization')).toBeInTheDocument();
    expect(screen.getByText('pause_organization')).toBeInTheDocument();
  });

  test('the admin-risk tool starts disabled while read tools start enabled', () => {
    render(<MCPConfig orgId="org-1" />);
    // pause_organization ships disabled (enabled: false) → its toggle offers "Enable".
    expect(screen.getByRole('button', { name: /Enable pause_organization/i })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    // start_mission ships enabled → offers "Disable".
    expect(screen.getByRole('button', { name: /Disable start_mission/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  test('toggling a tool flips its pressed state', async () => {
    render(<MCPConfig orgId="org-1" />);
    const enableBtn = screen.getByRole('button', { name: /Enable pause_organization/i });
    await userEvent.click(enableBtn);
    // After enabling, the accessible label flips to "Disable ..." and pressed → true.
    expect(screen.getByRole('button', { name: /Disable pause_organization/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  test('copy config writes the Claude Desktop JSON (with the endpoint) to the clipboard', async () => {
    render(<MCPConfig orgId="org-9" />);
    await userEvent.click(screen.getByRole('button', { name: /Copy Claude Desktop config/i }));
    expect(writeText).toHaveBeenCalledTimes(1);
    const payload = writeText.mock.calls[0][0] as string;
    expect(payload).toContain('agentverse-org');
    expect(payload).toContain('/v1/mcp/org-9');
    // Never leaks a real key — only the placeholder.
    expect(payload).toContain('<your-api-key>');
    // Confirmation feedback appears.
    expect(await screen.findByText('Copied')).toBeInTheDocument();
  });
});
