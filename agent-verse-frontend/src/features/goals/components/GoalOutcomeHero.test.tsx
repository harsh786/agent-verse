import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { GoalOutcomeHero } from './GoalOutcomeHero';
import type { ResultArtifact } from '../resultArtifact';

const artifact: ResultArtifact = {
  version: 1,
  kind: 'table',
  title: 'Jira issues',
  summary: 'Found 8 Jira issues.',
  status: 'success',
  metrics: [{ label: 'Issues', value: 8 }],
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
  downloads: ['json', 'csv', 'markdown'],
  debug: {},
};

function readBlob(blob: Blob): Promise<string> {
  if ('text' in blob && typeof blob.text === 'function') {
    return blob.text();
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener('load', () => resolve(String(reader.result)));
    reader.addEventListener('error', () => reject(reader.error));
    reader.readAsText(blob);
  });
}

describe('GoalOutcomeHero', () => {
  const originalCreateElement = document.createElement.bind(document);
  const originalAppendChild = document.body.appendChild.bind(document.body);
  const originalRemoveChild = document.body.removeChild.bind(document.body);
  let anchor: HTMLAnchorElement;
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;
  let appendChild: ReturnType<typeof vi.fn>;
  let removeChild: ReturnType<typeof vi.fn>;
  let clickAnchor: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: vi.fn().mockReturnValue(true),
    });
    createObjectURL = vi.fn().mockReturnValue('blob:goal-result');
    revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectURL,
    });
    anchor = document.createElement('a');
    clickAnchor = vi.spyOn(anchor, 'click').mockImplementation(() => undefined);
    appendChild = vi.fn((node: Node) => originalAppendChild(node));
    removeChild = vi.fn((node: Node) => originalRemoveChild(node));
    vi.spyOn(document.body, 'appendChild').mockImplementation(appendChild);
    vi.spyOn(document.body, 'removeChild').mockImplementation(removeChild);
    vi.spyOn(document, 'createElement').mockImplementation((tagName: string) => {
      if (tagName === 'a') return anchor;
      return originalCreateElement(tagName);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders result summary and actions', async () => {
    render(
      <GoalOutcomeHero
        goal="Fetch Jira"
        status="complete"
        artifact={artifact}
        onRerun={vi.fn()}
      />
    );

    expect(screen.getByText('Jira issues')).toBeInTheDocument();
    expect(screen.getByText('Found 8 Jira issues.')).toBeInTheDocument();
    expect(screen.getByText('Fetch Jira')).toBeInTheDocument();
    expect(screen.getByText('success')).toBeInTheDocument();
    expect(screen.getByText('Issues')).toBeInTheDocument();
    expect(screen.getByText('8')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy summary/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download json/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download csv/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download markdown/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /print/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /rerun goal/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /copy summary/i }));

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('Found 8 Jira issues.');
  });

  test('renders only downloads allowed by the artifact', () => {
    render(
      <GoalOutcomeHero
        goal="Fetch Jira"
        status="complete"
        artifact={{ ...artifact, downloads: ['csv'] }}
        onRerun={vi.fn()}
      />
    );

    expect(screen.queryByRole('button', { name: /download json/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download csv/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /download markdown/i })).not.toBeInTheDocument();
  });

  test('does not render CSV download when artifact allows CSV without a table', () => {
    render(
      <GoalOutcomeHero
        goal="Fetch Jira"
        status="complete"
        artifact={{ ...artifact, tables: [], downloads: ['csv'] }}
        onRerun={vi.fn()}
      />
    );

    expect(screen.queryByRole('button', { name: /download csv/i })).not.toBeInTheDocument();
  });

  test('downloads JSON with expected filename content and mime type', async () => {
    let revokeCallback: (() => void) | undefined;
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout').mockImplementation((handler) => {
      if (typeof handler === 'function') {
        revokeCallback = handler as () => void;
      }
      return 1 as unknown as ReturnType<typeof setTimeout>;
    });
    render(<GoalOutcomeHero goal="Fetch Jira" status="complete" artifact={artifact} onRerun={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /download json/i }));

    expect(createObjectURL).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'application/json' })
    );
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    await expect(readBlob(blob)).resolves.toBe(JSON.stringify(artifact, null, 2));
    expect(anchor.download).toBe('goal-result.json');
    expect(anchor.href).toBe('blob:goal-result');
    expect(appendChild).toHaveBeenCalledWith(anchor);
    expect(clickAnchor).toHaveBeenCalled();
    expect(removeChild).toHaveBeenCalledWith(anchor);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 0);

    revokeCallback?.();

    expect(revokeObjectURL).toHaveBeenCalledWith('blob:goal-result');
  });

  test('downloads CSV with expected filename content and mime type', async () => {
    render(<GoalOutcomeHero goal="Fetch Jira" status="complete" artifact={artifact} onRerun={vi.fn()} />);

    await userEvent.click(screen.getByRole('button', { name: /download csv/i }));

    expect(createObjectURL).toHaveBeenCalledWith(expect.objectContaining({ type: 'text/csv' }));
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    await expect(readBlob(blob)).resolves.toBe('Key,Summary\nPCF-58608,Deployment fix');
    expect(anchor.download).toBe('goal-result.csv');
  });

  test('downloads Markdown with expected filename content and mime type', async () => {
    render(<GoalOutcomeHero goal="Fetch Jira" status="complete" artifact={artifact} onRerun={vi.fn()} />);

    await userEvent.click(screen.getByRole('button', { name: /download markdown/i }));

    expect(createObjectURL).toHaveBeenCalledWith(expect.objectContaining({ type: 'text/markdown' }));
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    const markdown = await readBlob(blob);
    expect(markdown).toContain('# Jira issues\n\nFound 8 Jira issues.');
    expect(markdown).toContain('| Key | Summary |');
    expect(anchor.download).toBe('goal-result.md');
  });

  test('reruns the goal when the rerun action is clicked', async () => {
    const onRerun = vi.fn();
    render(<GoalOutcomeHero goal="Fetch Jira" status="complete" artifact={artifact} onRerun={onRerun} />);

    await userEvent.click(screen.getByRole('button', { name: /rerun goal/i }));

    expect(onRerun).toHaveBeenCalledTimes(1);
  });

  test('falls back to textarea copy when clipboard writeText is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {},
    });
    const execCommand = vi.spyOn(document, 'execCommand').mockReturnValue(true);
    render(<GoalOutcomeHero goal="Fetch Jira" status="complete" artifact={artifact} onRerun={vi.fn()} />);

    await userEvent.click(screen.getByRole('button', { name: /copy summary/i }));

    await waitFor(() => expect(execCommand).toHaveBeenCalledWith('copy'));
    expect(screen.queryByDisplayValue('Found 8 Jira issues.')).not.toBeInTheDocument();
  });

  test('does not throw when clipboard and fallback copy fail', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('blocked')) },
    });
    vi.spyOn(document, 'execCommand').mockImplementation(() => {
      throw new Error('denied');
    });
    render(<GoalOutcomeHero goal="Fetch Jira" status="complete" artifact={artifact} onRerun={vi.fn()} />);

    await expect(userEvent.click(screen.getByRole('button', { name: /copy summary/i }))).resolves.toBe(
      undefined
    );
  });
});
