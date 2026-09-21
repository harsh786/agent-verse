import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';
import { ToolCallInspector } from './ToolCallInspector';

describe('ToolCallInspector', () => {
  test('renders successful tool output as an adaptive table with links', async () => {
    const user = userEvent.setup();

    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_complete',
            tool_name: 'github_search_issues',
            success: true,
            output: {
              total_count: 1,
              items: [
                {
                  number: 1842,
                  title: 'Fix auth middleware',
                  state: 'open',
                  user: { login: 'octocat' },
                  html_url: 'https://github.com/acme/repo/pull/1842',
                },
              ],
            },
          },
        ]}
      />
    );

    await user.click(screen.getByRole('button', { name: /github_search_issues/i }));

    expect(screen.getByRole('table', { name: /items/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '1842' })).toHaveAttribute(
      'href',
      'https://github.com/acme/repo/pull/1842'
    );
    expect(screen.getByText('Fix auth middleware')).toBeInTheDocument();
    expect(screen.getByText('octocat')).toBeInTheDocument();
  });

  test('renders failed tool output through the adaptive panel and keeps the error block', async () => {
    const user = userEvent.setup();

    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            server_id: 'jira',
            success: false,
            output: { error: 'HTTP 401: Unauthorized' },
            error: 'HTTP 401: Unauthorized',
          },
        ]}
      />
    );

    await user.click(screen.getByRole('button', { name: /jira_search_issues/i }));

    const adaptivePanel = screen.getByRole('region', { name: /jira_search_issues failed/i });
    expect(within(adaptivePanel).getByRole('heading', { name: /jira_search_issues failed/i })).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('Tool')).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('jira_search_issues')).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('Server')).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('jira')).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('Error')).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('HTTP 401: Unauthorized')).toBeInTheDocument();
    expect(screen.getAllByText('HTTP 401: Unauthorized').length).toBeGreaterThan(1);
  });

  test('renders failed tool errors through the adaptive panel when output is absent', async () => {
    const user = userEvent.setup();

    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_failed',
            tool_name: 'github_create_pr',
            server_id: 'github',
            error: 'Token expired',
          },
        ]}
      />
    );

    await user.click(screen.getByRole('button', { name: /github_create_pr/i }));

    const adaptivePanel = screen.getByRole('region', { name: /github_create_pr failed/i });
    expect(within(adaptivePanel).getByRole('heading', { name: /github_create_pr failed/i })).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('Server')).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('github')).toBeInTheDocument();
    expect(within(adaptivePanel).getByText('Token expired')).toBeInTheDocument();
    expect(screen.getAllByText('Token expired').length).toBeGreaterThan(1);
  });

  test('shows tool_call_failed events without success false as failed in the list', () => {
    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_failed',
            tool_name: 'github_create_pr',
            server_id: 'github',
            error: 'Token expired',
          },
        ]}
      />
    );

    const toolButton = screen.getByRole('button', { name: /github_create_pr/i });
    expect(within(toolButton).getByText('failed')).toBeInTheDocument();
  });

  test('returns null when there are no tool events', () => {
    const { container } = render(<ToolCallInspector toolEvents={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  test('pluralizes the tool call count and shows a placeholder before selection', () => {
    render(
      <ToolCallInspector
        toolEvents={[
          { type: 'tool_call_complete', tool_name: 'tool_one', success: true },
          { type: 'tool_call_complete', tool_name: 'tool_two', success: true },
        ]}
      />
    );

    expect(screen.getByText('2 tool calls')).toBeInTheDocument();
    expect(screen.getByText('Select a tool call to inspect')).toBeInTheDocument();
  });

  test('shows latency in both the list item and the detail panel, and shows the timestamp', async () => {
    const user = userEvent.setup();

    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_complete',
            tool_name: 'slow_tool',
            success: true,
            latency_ms: 1234,
            ts: '2024-05-01T12:34:56.789Z',
          },
        ]}
      />
    );

    const toolButton = screen.getByRole('button', { name: /slow_tool/i });
    expect(within(toolButton).getByText('1234ms')).toBeInTheDocument();

    await user.click(toolButton);

    expect(screen.getByText('1234ms latency')).toBeInTheDocument();
    expect(screen.getByText('2024-05-01T12:34:56')).toBeInTheDocument();
  });

  test('shows a risk badge when the event carries a risk level', async () => {
    const user = userEvent.setup();

    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_complete',
            tool_name: 'delete_resource',
            success: true,
            risk: 'critical',
          },
        ]}
      />
    );

    await user.click(screen.getByRole('button', { name: /delete_resource/i }));

    expect(screen.getByText('critical')).toHaveClass('text-red-600');
  });

  test('falls back to the "tool" field and "Unknown Tool" label when tool_name is absent', async () => {
    const user = userEvent.setup();

    render(
      <ToolCallInspector
        toolEvents={[
          { type: 'tool_call_complete', tool: 'legacy_tool', success: true },
          { type: 'tool_call_complete', success: true },
        ]}
      />
    );

    expect(screen.getByRole('button', { name: /legacy_tool/i })).toBeInTheDocument();
    const unknownButton = screen.getByRole('button', { name: /^unknown success$/i });
    expect(unknownButton).toBeInTheDocument();

    await user.click(unknownButton);
    expect(screen.getByRole('heading', { name: /unknown tool/i })).toBeInTheDocument();
  });

  test('falls back to String() when arguments cannot be JSON-serialized', async () => {
    const user = userEvent.setup();
    const circular: Record<string, unknown> = { name: 'circular' };
    circular.self = circular;

    render(
      <ToolCallInspector
        toolEvents={[
          {
            type: 'tool_call_complete',
            tool_name: 'weird_args_tool',
            success: true,
            arguments: circular,
          },
        ]}
      />
    );

    await user.click(screen.getByRole('button', { name: /weird_args_tool/i }));

    expect(screen.getByText('[object Object]')).toBeInTheDocument();
  });

  test('renders no output or error section when a successful call has neither', async () => {
    const user = userEvent.setup();

    render(
      <ToolCallInspector
        toolEvents={[
          { type: 'tool_call_complete', tool_name: 'no_output_tool', success: true },
        ]}
      />
    );

    await user.click(screen.getByRole('button', { name: /no_output_tool/i }));

    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('Error')).not.toBeInTheDocument();
    expect(screen.queryByRole('region')).not.toBeInTheDocument();
  });
});
