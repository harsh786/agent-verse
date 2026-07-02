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
});
