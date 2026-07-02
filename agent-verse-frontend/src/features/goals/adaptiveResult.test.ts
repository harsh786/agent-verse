import { describe, expect, test } from 'vitest';
import { normalizeAdaptiveResult } from './adaptiveResult';

describe('normalizeAdaptiveResult', () => {
  test('normalizes Jira-style issue output to a table', () => {
    const result = normalizeAdaptiveResult({
      total: 10,
      max_results: 50,
      issues: [
        {
          key: 'AV-123',
          summary: 'Render adaptive results',
          status: 'In Progress',
          assignee: 'Harsh',
          updated: '2026-07-01T12:00:00Z',
        },
      ],
    });

    expect(result.status).toBe('success');
    expect(result.primaryView).toBe('table');
    expect(result.metrics).toContainEqual({ label: 'Total', value: 10 });
    expect(result.metrics).toContainEqual({ label: 'Returned', value: 1 });
    expect(result.table?.columns.map((column) => column.key)).toEqual([
      'key',
      'summary',
      'status',
      'assignee',
      'updated',
    ]);
    expect(result.table?.rows[0]?.key).toBe('AV-123');
  });

  test('normalizes GitHub-style search output to table rows with owner and url', () => {
    const result = normalizeAdaptiveResult({
      total_count: 2,
      items: [
        {
          number: 42,
          title: 'Improve rendering',
          state: 'open',
          user: { login: 'octocat' },
          html_url: 'https://github.com/octo/repo/issues/42',
        },
      ],
    });

    expect(result.primaryView).toBe('table');
    expect(result.metrics).toContainEqual({ label: 'Total', value: 2 });
    expect(result.table?.columns.find((column) => column.key === 'number')?.type).toBe('link');
    expect(result.table?.rows[0]?.owner).toBe('octocat');
    expect(result.table?.rows[0]?.url).toBe('https://github.com/octo/repo/issues/42');
  });

  test('keeps null error payloads with items as table results', () => {
    const result = normalizeAdaptiveResult({
      error: null,
      items: [{ id: 'one', name: 'First item' }],
    });

    expect(result.status).toBe('success');
    expect(result.primaryView).toBe('table');
    expect(result.table?.rows[0]?.id).toBe('one');
  });

  test('keeps false error payloads with results as table results', () => {
    const result = normalizeAdaptiveResult({
      error: false,
      results: [{ id: 'two', name: 'Second item' }],
    });

    expect(result.status).toBe('success');
    expect(result.primaryView).toBe('table');
    expect(result.table?.rows[0]?.id).toBe('two');
  });

  test('normalizes Linear-style nested issues to table rows', () => {
    const result = normalizeAdaptiveResult({
      data: {
        issues: {
          nodes: [
            {
              identifier: 'ENG-91',
              title: 'Handle connector results',
              state: { name: 'Backlog' },
              assignee: { name: 'Avery' },
            },
          ],
        },
      },
    });

    expect(result.primaryView).toBe('table');
    expect(result.table?.rows[0]?.status).toBe('Backlog');
    expect(result.table?.rows[0]?.assignee).toBe('Avery');
  });

  test('normalizes Slack-style messages with preserved text values', () => {
    const result = normalizeAdaptiveResult({
      messages: [{ user: 'U123', text: 'Deployment completed', ts: '1782960000.000100' }],
    });

    expect(result.primaryView).toBe('table');
    expect(result.table?.columns.some((column) => column.key === 'text')).toBe(true);
    expect(result.table?.rows[0]?.text).toBe('Deployment completed');
  });

  test('normalizes plain string output as text', () => {
    const result = normalizeAdaptiveResult('Plain connector response');

    expect(result.primaryView).toBe('text');
    expect(result.text).toBe('Plain connector response');
  });

  test('normalizes structured JSON string output as a table', () => {
    const result = normalizeAdaptiveResult('{"total":1,"issues":[{"key":"OPP-1","summary":"JSON string"}]}');

    expect(result.primaryView).toBe('table');
    expect(result.metrics).toContainEqual({ label: 'Total', value: 1 });
    expect(result.table?.rows[0]?.key).toBe('OPP-1');
    expect(result.table?.rows[0]?.summary).toBe('JSON string');
  });

  test('normalizes empty structured JSON string output as diagnostics', () => {
    const result = normalizeAdaptiveResult('{"total":0,"issues":[],"jql":"project = OPP"}');

    expect(result.status).toBe('empty');
    expect(result.primaryView).toBe('diagnostic');
    expect(result.diagnostics).toContainEqual({ label: 'Query', value: 'project = OPP' });
  });

  test('normalizes failed output as diagnostics', () => {
    const result = normalizeAdaptiveResult(
      { error: 'HTTP 401: Unauthorized' },
      { toolName: 'jira_search_issues', serverId: 'jira', success: false }
    );

    expect(result.status).toBe('failed');
    expect(result.primaryView).toBe('diagnostic');
    expect(result.diagnostics).toContainEqual({ label: 'Tool', value: 'jira_search_issues' });
    expect(result.diagnostics).toContainEqual({ label: 'Error', value: 'HTTP 401: Unauthorized' });
  });

  test('normalizes empty successful arrays as diagnostics', () => {
    const result = normalizeAdaptiveResult({
      total: 0,
      issues: [],
      jql: 'assignee = "Nobody"',
    });

    expect(result.status).toBe('empty');
    expect(result.primaryView).toBe('diagnostic');
    expect(result.diagnostics).toContainEqual({ label: 'Query', value: 'assignee = "Nobody"' });
    expect(result.diagnostics.some((diagnostic) => diagnostic.value.includes('No rows'))).toBe(
      true
    );
  });

  test('falls back to json for unknown nested objects', () => {
    const output = { nested: { value: { deep: true } } };

    const result = normalizeAdaptiveResult(output);

    expect(result.primaryView).toBe('json');
    expect(result.raw).toBe(output);
  });
});
