import { describe, expect, test } from 'vitest';
import { artifactToCsv, artifactToMarkdown, type ResultArtifact } from './resultArtifact';

describe('resultArtifact exports', () => {
  const artifact: ResultArtifact = {
    version: 1,
    kind: 'table',
    title: 'Jira issues',
    summary: 'Found 2 Jira issues.',
    status: 'success',
    metrics: [],
    tables: [
      {
        title: 'Issues',
        columns: [
          { key: 'key', label: 'Key', type: 'link' },
          { key: 'summary', label: 'Summary', type: 'text' },
        ],
        rows: [
          { key: 'PCF-58608', summary: 'Deployment fix' },
          { key: 'OPP-32778', summary: 'Invoice tables' },
        ],
      },
    ],
    evidence: { tools: [], verification: '' },
    downloads: ['json', 'csv', 'markdown'],
    debug: { event_count: 3 },
  };

  test('artifactToCsv exports first table', () => {
    expect(artifactToCsv(artifact)).toContain('Key,Summary');
    expect(artifactToCsv(artifact)).toContain('PCF-58608,Deployment fix');
  });

  test('artifactToMarkdown exports summary and table', () => {
    expect(artifactToMarkdown(artifact)).toContain('# Jira issues');
    expect(artifactToMarkdown(artifact)).toContain('Found 2 Jira issues.');
    expect(artifactToMarkdown(artifact)).toContain('| Key | Summary |');
  });

  test('artifactToCsv escapes commas, quotes, and newlines', () => {
    const escapedArtifact: ResultArtifact = {
      ...artifact,
      tables: [
        {
          title: 'Escaped values',
          columns: [
            { key: 'summary', label: 'Summary', type: 'text' },
            { key: 'notes', label: 'Notes', type: 'text' },
          ],
          rows: [
            {
              summary: 'Needs, review',
              notes: 'Line one\n"quoted" line two',
            },
          ],
        },
      ],
    };

    expect(artifactToCsv(escapedArtifact)).toBe(
      'Summary,Notes\n"Needs, review","Line one\n""quoted"" line two"'
    );
  });

  test('artifactToCsv escapes carriage returns', () => {
    const escapedArtifact: ResultArtifact = {
      ...artifact,
      tables: [
        {
          title: 'Escaped values',
          columns: [{ key: 'notes', label: 'Notes', type: 'text' }],
          rows: [{ notes: 'Line one\rline two' }],
        },
      ],
    };

    expect(artifactToCsv(escapedArtifact)).toBe('Notes\n"Line one\rline two"');
  });

  test('artifactToMarkdown escapes unsafe markdown and html content', () => {
    const escapedArtifact: ResultArtifact = {
      ...artifact,
      title: '# <script>alert("title")</script>',
      summary: 'Summary with **bold**\nand <img src=x onerror=alert(1)>',
      tables: [
        {
          title: 'Table | <b>title</b>',
          columns: [
            { key: 'name', label: 'Name | **label**', type: 'text' },
            { key: 'notes', label: 'Notes\n<script>', type: 'text' },
          ],
          rows: [
            {
              name: 'Alice | **admin**',
              notes: 'Line one\nline two <script>alert(1)</script>',
            },
          ],
        },
      ],
    };

    expect(artifactToMarkdown(escapedArtifact)).toBe(
      [
        '# \\# &lt;script&gt;alert("title")&lt;/script&gt;',
        '',
        'Summary with \\*\\*bold\\*\\* and &lt;img src=x onerror=alert(1)&gt;',
        '',
        '## Table \\| &lt;b&gt;title&lt;/b&gt;',
        '',
        '| Name \\| \\*\\*label\\*\\* | Notes &lt;script&gt; |',
        '| --- | --- |',
        '| Alice \\| \\*\\*admin\\*\\* | Line one line two &lt;script&gt;alert(1)&lt;/script&gt; |',
      ].join('\n')
    );
  });
});
