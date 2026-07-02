import { vi, describe, test, expect, afterEach } from 'vitest';
import { openPrintView } from './GoalPrintView';
import type { ResultArtifact } from '../resultArtifact';

const ARTIFACT: ResultArtifact = {
  version: 1,
  kind: 'table',
  title: 'Jira issues',
  summary: 'Found 2 issues.',
  status: 'success',
  metrics: [{ label: 'Issues', value: 2 }],
  tables: [
    {
      title: 'Issues',
      columns: [
        { key: 'key', label: 'Key', type: 'link' },
        { key: 'summary', label: 'Summary', type: 'text' },
      ],
      rows: [
        { key: 'OPP-1', summary: 'Bug fix' },
        { key: 'OPP-2', summary: 'Feature' },
      ],
    },
  ],
  evidence: {},
  downloads: ['json', 'csv', 'markdown'],
  debug: {},
};

describe('GoalPrintView', () => {
  afterEach(() => vi.restoreAllMocks());

  test('opens a new window instead of calling window.print()', () => {
    const windowPrintMock = vi.fn();
    window.print = windowPrintMock;

    const mockPrintWin = {
      document: { write: vi.fn(), close: vi.fn() },
      print: vi.fn(),
      focus: vi.fn(),
      close: vi.fn(),
    };
    const windowOpenMock = vi.spyOn(window, 'open').mockReturnValue(
      mockPrintWin as unknown as Window
    );

    openPrintView(ARTIFACT, 'Find all Jira issues');

    expect(windowOpenMock).toHaveBeenCalledWith('', '_blank', 'width=900,height=700');
    expect(mockPrintWin.document.write).toHaveBeenCalledTimes(1);
    const writtenHtml = mockPrintWin.document.write.mock.calls[0][0] as string;
    expect(writtenHtml).toContain('Jira issues');
    expect(writtenHtml).toContain('OPP-1');
    expect(writtenHtml).toContain('Found 2 issues.');
    expect(windowPrintMock).not.toHaveBeenCalled();
  });

  test('writes artifact title into the print document', () => {
    const mockPrintWin = { document: { write: vi.fn(), close: vi.fn() }, focus: vi.fn() };
    vi.spyOn(window, 'open').mockReturnValue(mockPrintWin as unknown as Window);

    openPrintView(ARTIFACT, 'my goal');
    const html = mockPrintWin.document.write.mock.calls[0][0] as string;
    expect(html).toContain('<title>Jira issues</title>');
    expect(html).toContain('my goal');
    expect(html).toContain('Issues');
  });

  test('renders table rows in print HTML', () => {
    const mockPrintWin = { document: { write: vi.fn(), close: vi.fn() }, focus: vi.fn() };
    vi.spyOn(window, 'open').mockReturnValue(mockPrintWin as unknown as Window);

    openPrintView(ARTIFACT, 'goal');
    const html = mockPrintWin.document.write.mock.calls[0][0] as string;
    expect(html).toContain('OPP-1');
    expect(html).toContain('OPP-2');
    expect(html).toContain('Bug fix');
  });
});
