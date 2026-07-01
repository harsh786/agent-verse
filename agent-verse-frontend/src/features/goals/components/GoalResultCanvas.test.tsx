import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, test, vi } from 'vitest';
import { GoalResultCanvas } from './GoalResultCanvas';
import type { ResultArtifact } from '../resultArtifact';

describe('GoalResultCanvas', () => {
  test('renders Jira table rows', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'table',
          title: 'Jira issues',
          summary: 'Found 1 Jira issue.',
          status: 'success',
          metrics: [],
          tables: [
            {
              title: 'Issues',
              columns: [
                { key: 'key', label: 'Key', type: 'link' },
                { key: 'summary', label: 'Summary', type: 'text' },
                { key: 'status', label: 'Status', type: 'badge' },
              ],
              rows: [{ key: 'PCF-58608', summary: 'Deployment fix', status: 'Closed' }],
            },
          ],
          evidence: {},
          downloads: ['json', 'csv'],
          debug: {},
        }}
      />
    );

    expect(screen.getByRole('columnheader', { name: 'Key' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Summary' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Status' })).toBeInTheDocument();
    expect(screen.getByText('PCF-58608')).toBeInTheDocument();
    expect(screen.getByText('Deployment fix')).toBeInTheDocument();
    expect(screen.getByText('Closed')).toBeInTheDocument();
  });

  test('renders empty artifacts', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'empty',
          title: 'No data',
          summary: 'No matching Jira issues were found.',
          status: 'empty',
          metrics: [],
          tables: [],
          evidence: {},
          downloads: [],
          debug: {},
        }}
      />
    );

    expect(screen.getByRole('heading', { name: 'No data' })).toBeInTheDocument();
    expect(screen.getByText('No matching Jira issues were found.')).toBeInTheDocument();
  });

  test('renders default empty artifact copy when title and summary are absent', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'empty',
          title: '',
          summary: '',
          status: 'empty',
          metrics: [],
          tables: [],
          evidence: {},
          downloads: [],
          debug: {},
        }}
      />
    );

    expect(screen.getByRole('heading', { name: 'No matching results' })).toBeInTheDocument();
    expect(
      screen.getByText('The agent completed successfully, but the source returned no rows.')
    ).toBeInTheDocument();
  });

  test('renders failed artifacts as a diagnostic card', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'error',
          title: 'Jira search failed',
          summary: 'Jira returned a 401 while searching assigned issues.',
          status: 'failed',
          metrics: [],
          tables: [],
          evidence: {
            tools: [
              { name: 'jira_auth_check', success: true },
              { name: 'jira_search_issues', success: false },
            ],
            verification: 'Reconnect Jira and rerun the goal.',
          },
          downloads: ['json'],
          debug: {},
        }}
      />
    );

    expect(screen.getByRole('heading', { name: 'Jira search failed' })).toBeInTheDocument();
    expect(screen.getByText('Jira returned a 401 while searching assigned issues.')).toBeInTheDocument();
    expect(screen.getByText('jira_auth_check')).toBeInTheDocument();
    expect(screen.getByText('Reconnect Jira and rerun the goal.')).toBeInTheDocument();
    expect(screen.getByText(/Use the Execution tab/i)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Use the Execution tab/i })).not.toBeInTheDocument();
  });

  test('renders an Execution tab button for failed artifacts when a callback is provided', async () => {
    const onShowExecution = vi.fn();

    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'error',
          title: 'Jira search failed',
          summary: 'Jira returned a 401 while searching assigned issues.',
          status: 'failed',
          metrics: [],
          tables: [],
          evidence: {
            tools: [{ name: 'jira_auth_check', success: true }],
            verification: 'Reconnect Jira and rerun the goal.',
          },
          downloads: ['json'],
          debug: {},
        }}
        onShowExecution={onShowExecution}
      />
    );

    const button = screen.getByRole('button', { name: /open execution tab/i });
    expect(button).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /execution tab/i })).not.toBeInTheDocument();

    await userEvent.click(button);

    expect(onShowExecution).toHaveBeenCalledTimes(1);
  });

  test('renders useful partial results with an incomplete warning', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'table',
          title: 'Jira issues',
          summary: 'Found 1 issue before Jira rate limited the request.',
          status: 'partial',
          metrics: [],
          tables: [
            {
              title: 'Issues',
              columns: [
                { key: 'key', label: 'Key', type: 'link' },
                { key: 'summary', label: 'Summary', type: 'text' },
              ],
              rows: [{ key: 'PCF-58608', summary: 'Deployment fix' }],
            },
          ],
          evidence: {},
          downloads: ['json', 'csv'],
          debug: {},
        }}
      />
    );

    expect(screen.getByText('PCF-58608')).toBeInTheDocument();
    expect(screen.getByText('Deployment fix')).toBeInTheDocument();
    expect(screen.getByText(/result may be incomplete/i)).toBeInTheDocument();
  });

  test('labels tables with the visible table heading', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'table',
          title: 'Jira issues',
          summary: 'Found 1 Jira issue.',
          status: 'success',
          metrics: [],
          tables: [
            {
              title: 'Issues',
              columns: [{ key: 'key', label: 'Key', type: 'text' }],
              rows: [{ key: 'PCF-58608' }],
            },
          ],
          evidence: {},
          downloads: ['json'],
          debug: {},
        }}
      />
    );

    expect(screen.getByRole('table', { name: 'Issues' })).toBeInTheDocument();
  });

  test('renders link columns as links when a row url is available', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'table',
          title: 'Jira issues',
          summary: 'Found 1 Jira issue.',
          status: 'success',
          metrics: [],
          tables: [
            {
              title: 'Issues',
              columns: [{ key: 'key', label: 'Key', type: 'link' }],
              rows: [{ key: 'PCF-58608', url: 'https://jira.example.com/browse/PCF-58608' }],
            },
          ],
          evidence: {},
          downloads: ['json'],
          debug: {},
        }}
      />
    );

    expect(screen.getByRole('link', { name: 'PCF-58608' })).toHaveAttribute(
      'href',
      'https://jira.example.com/browse/PCF-58608'
    );
  });

  test('renders link columns as text when no row url is available', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'table',
          title: 'Jira issues',
          summary: 'Found 1 Jira issue.',
          status: 'success',
          metrics: [],
          tables: [
            {
              title: 'Issues',
              columns: [{ key: 'key', label: 'Key', type: 'link' }],
              rows: [{ key: 'PCF-58608' }],
            },
          ],
          evidence: {},
          downloads: ['json'],
          debug: {},
        }}
      />
    );

    expect(screen.getByText('PCF-58608')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'PCF-58608' })).not.toBeInTheDocument();
  });

  test('renders datetime cells with machine-readable time elements', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'table',
          title: 'Jira issues',
          summary: 'Found 1 Jira issue.',
          status: 'success',
          metrics: [],
          tables: [
            {
              title: 'Issues',
              columns: [{ key: 'updated', label: 'Updated', type: 'datetime' }],
              rows: [{ updated: '2026-07-01T10:30:00.000Z' }],
            },
          ],
          evidence: {},
          downloads: ['json'],
          debug: {},
        }}
      />
    );

    expect(screen.getByText('2026-07-01T10:30:00.000Z').tagName).toBe('TIME');
    expect(screen.getByText('2026-07-01T10:30:00.000Z')).toHaveAttribute(
      'dateTime',
      '2026-07-01T10:30:00.000Z'
    );
  });

  test('right-aligns number cells and allows long text to wrap', () => {
    render(
      <GoalResultCanvas
        artifact={{
          version: 1,
          kind: 'table',
          title: 'Jira issues',
          summary: 'Found 1 Jira issue.',
          status: 'success',
          metrics: [],
          tables: [
            {
              title: 'Issues',
              columns: [
                { key: 'points', label: 'Points', type: 'number' },
                { key: 'summary', label: 'Summary', type: 'text' },
              ],
              rows: [
                {
                  points: 13,
                  summary: 'averylongunbrokenissuetitlethatshouldwrapinsteadofoverflowing',
                },
              ],
            },
          ],
          evidence: {},
          downloads: ['json'],
          debug: {},
        }}
      />
    );

    expect(screen.getByText('13').closest('td')).toHaveClass('text-right');
    expect(
      screen.getByText('averylongunbrokenissuetitlethatshouldwrapinsteadofoverflowing')
    ).toHaveClass('break-words');
  });

  test('renders text artifact fallback', () => {
    const artifact: ResultArtifact = {
      version: 1,
      kind: 'text',
      title: 'Goal result',
      summary: 'The agent completed the requested cleanup.\nNo issues remain.',
      status: 'success',
      metrics: [],
      tables: [],
      evidence: {},
      downloads: ['json', 'markdown'],
      debug: {},
    };

    render(<GoalResultCanvas artifact={artifact} />);

    expect(screen.getByText(/The agent completed the requested cleanup/)).toBeInTheDocument();
    expect(screen.getByText(/No issues remain/)).toBeInTheDocument();
    expect(screen.getByText(/The agent completed the requested cleanup/)).toHaveClass(
      'break-words'
    );
  });
});
