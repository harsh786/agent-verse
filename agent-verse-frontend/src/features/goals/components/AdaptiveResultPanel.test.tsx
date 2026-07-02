import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test } from 'vitest';
import type { AdaptiveResultViewModel } from '../adaptiveResult';
import { AdaptiveResultPanel } from './AdaptiveResultPanel';

describe('AdaptiveResultPanel', () => {
  test('renders table results with metrics, accessible table, and row links', () => {
    const result: AdaptiveResultViewModel = {
      status: 'success',
      title: 'jira_search_issues results',
      metrics: [
        { label: 'Total', value: 10 },
        { label: 'Returned', value: 1 },
      ],
      primaryView: 'table',
      table: {
        title: 'Issues',
        columns: [
          { key: 'key', label: 'Key', type: 'link' },
          { key: 'summary', label: 'Summary', type: 'text' },
          { key: 'status', label: 'Status', type: 'badge' },
        ],
        rows: [
          {
            key: 'OPP-34746',
            summary: 'Unable to reconcile the connector payload',
            status: 'Open',
            url: 'https://jira.example.com/browse/OPP-34746',
          },
        ],
      },
      diagnostics: [],
      raw: {
        total: 10,
        issues: [{ key: 'OPP-34746' }],
      },
    };

    render(<AdaptiveResultPanel result={result} />);

    expect(screen.getByRole('heading', { name: 'jira_search_issues results' })).toBeInTheDocument();
    expect(screen.getByText('Total')).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Issues' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'OPP-34746' })).toHaveAttribute(
      'href',
      'https://jira.example.com/browse/OPP-34746'
    );
    expect(screen.getByText('Unable to reconcile the connector payload')).toBeInTheDocument();
  });

  test('renders text output', () => {
    render(
      <AdaptiveResultPanel
        result={{
          status: 'success',
          title: 'Health check',
          metrics: [],
          primaryView: 'text',
          text: 'All checks passed.',
          diagnostics: [],
          raw: 'All checks passed.',
        }}
      />
    );

    expect(screen.getByText('All checks passed.')).toBeInTheDocument();
  });

  test('renders direct URL values as links for link columns', () => {
    render(
      <AdaptiveResultPanel
        result={{
          status: 'success',
          title: 'Links',
          metrics: [],
          primaryView: 'table',
          table: {
            title: 'Pull requests',
            columns: [{ key: 'permalink', label: 'Permalink', type: 'link' }],
            rows: [{ permalink: 'https://github.com/acme/repo/pull/42' }],
          },
          diagnostics: [],
          raw: { permalink: 'https://github.com/acme/repo/pull/42' },
        }}
      />
    );

    expect(screen.getByRole('link', { name: 'https://github.com/acme/repo/pull/42' })).toHaveAttribute(
      'href',
      'https://github.com/acme/repo/pull/42'
    );
  });

  test('renders unsafe row URL fallbacks as plain text', () => {
    render(
      <AdaptiveResultPanel
        result={{
          status: 'success',
          title: 'Issues',
          metrics: [],
          primaryView: 'table',
          table: {
            title: 'Unsafe links',
            columns: [{ key: 'key', label: 'Key', type: 'link' }],
            rows: [{ key: 'OPP-34746', url: 'javascript:alert(1)' }],
          },
          diagnostics: [],
          raw: { key: 'OPP-34746', url: 'javascript:alert(1)' },
        }}
      />
    );

    expect(screen.queryByRole('link', { name: 'OPP-34746' })).not.toBeInTheDocument();
    expect(screen.getByText('OPP-34746')).toBeInTheDocument();
  });

  test('renders query and next-action diagnostics', () => {
    render(
      <AdaptiveResultPanel
        result={{
          status: 'empty',
          title: 'No Jira issues',
          metrics: [],
          primaryView: 'diagnostic',
          diagnostics: [
            { label: 'Query', value: 'assignee = currentUser() AND status = Open' },
            { label: 'Next action', value: 'Broaden the search criteria and rerun the goal.' },
          ],
          raw: { total: 0, issues: [] },
        }}
      />
    );

    expect(screen.getByText('Query')).toBeInTheDocument();
    expect(screen.getByText('assignee = currentUser() AND status = Open')).toBeInTheDocument();
    expect(screen.getByText('Broaden the search criteria and rerun the goal.')).toBeInTheDocument();
  });

  test('reveals raw output JSON from a disclosure', async () => {
    render(
      <AdaptiveResultPanel
        result={{
          status: 'success',
          title: 'jira_search_issues results',
          metrics: [],
          primaryView: 'json',
          diagnostics: [],
          raw: { issues: [{ key: 'OPP-34746' }] },
        }}
      />
    );

    await userEvent.click(screen.getByRole('button', { name: /raw output/i }));

    expect(screen.getByText(/"issues"/)).toBeInTheDocument();
  });
});
