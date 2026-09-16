import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, it, expect, vi } from 'vitest';
import { MissionDeliverable } from './MissionDeliverable';

describe('MissionDeliverable', () => {
  it('renders a string output as a deliverable card with its text', () => {
    render(
      <MissionDeliverable
        outputs={['Q3 revenue summary drafted']}
        evidence={[]}
      />,
    );
    expect(screen.getByText(/deliverable/i)).toBeInTheDocument();
    expect(screen.getByText(/q3 revenue summary drafted/i)).toBeInTheDocument();
  });

  it('unwraps a structured deliverable (summary) from an output entry', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { title: 'Weekly digest', summary: 'Three bullets here' } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('Weekly digest')).toBeInTheDocument();
    expect(screen.getByText(/three bullets here/i)).toBeInTheDocument();
  });

  it('renders typed artifacts — files, images and links — distinctly', () => {
    render(
      <MissionDeliverable
        outputs={[
          {
            deliverable: {
              summary: 'Report ready',
              files: [{ filename: 'report.pdf', url: 'https://x.test/report.pdf', content_type: 'application/pdf' }],
              images: [{ url: 'https://x.test/chart.png', caption: 'Revenue chart' }],
              links: [{ url: 'https://x.test/dashboard', title: 'Live dashboard' }],
            },
          },
        ]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('report.pdf')).toBeInTheDocument();
    expect(screen.getByAltText('Revenue chart')).toBeInTheDocument();
    expect(screen.getByText('Live dashboard')).toBeInTheDocument();
  });

  it('renders evidence items with links rather than a bare count', () => {
    render(
      <MissionDeliverable
        outputs={['done']}
        evidence={['https://src.test/proof', { label: 'Source', url: 'https://src.test/doc' }]}
      />,
    );
    expect(screen.getByText(/evidence/i)).toBeInTheDocument();
    expect(screen.getByText('https://src.test/proof')).toBeInTheDocument();
    expect(screen.getByText(/source/i)).toBeInTheDocument();
  });

  it('shows a "published to" receipt with a live link when the deliverable was published', () => {
    render(
      <MissionDeliverable
        outputs={['posted']}
        evidence={[]}
        published={{
          server_id: 'builtin-utility',
          tool_name: 'http_request',
          success: true,
          output: { url: 'https://social.test/post/42' },
          published_at: '2026-09-11T10:07:47Z',
        }}
      />,
    );
    expect(screen.getByText(/published/i)).toBeInTheDocument();
    expect(screen.getByText('https://social.test/post/42')).toBeInTheDocument();
  });

  it('shows an approval-pending banner when publishing awaits approval', () => {
    render(<MissionDeliverable outputs={[]} evidence={[]} publishPending />);
    expect(screen.getByText(/awaiting your approval/i)).toBeInTheDocument();
  });

  it('renders nothing when there is no deliverable, receipt, or pending publish', () => {
    const { container } = render(<MissionDeliverable outputs={[]} evidence={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders metric cards, including an object value serialized as JSON', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: {
          summary: 'ok',
          metrics: [
            { label: 'Revenue', value: 1200 },
            { label: 'Breakdown', value: { a: 1 } },
            { label: 'Missing' }, // value undefined → em dash
          ],
        } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('Revenue')).toBeInTheDocument();
    expect(screen.getByText('1200')).toBeInTheDocument();
    expect(screen.getByText('{"a":1}')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('renders a code-kind deliverable in a <pre><code> block instead of markdown', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { kind: 'code', content: 'console.log(1)' } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('console.log(1)').closest('pre')).not.toBeNull();
  });

  it('renders a {columns, rows} table with its title', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: {
          summary: 'see table',
          tables: [{ title: 'Q3 Numbers', columns: ['Metric', 'Value'], rows: [['Revenue', 100], ['Cost', { usd: 40 }]] }],
        } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('Q3 Numbers')).toBeInTheDocument();
    expect(screen.getByText('Metric')).toBeInTheDocument();
    expect(screen.getByText('Revenue')).toBeInTheDocument();
    expect(screen.getByText('{"usd":40}')).toBeInTheDocument();
  });

  it('renders an array-of-objects table by deriving columns from the first row', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: {
          summary: 'see table',
          tables: [[{ name: 'Alice', score: 9 }, { name: 'Bob', score: 7 }]],
        } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('name')).toBeInTheDocument();
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
  });

  it('omits an empty table (no columns and no rows)', () => {
    const { container } = render(
      <MissionDeliverable
        outputs={[{ deliverable: { summary: 'ok', tables: [{ title: 'Empty' }] } }]}
        evidence={[]}
      />,
    );
    expect(screen.queryByText('Empty')).not.toBeInTheDocument();
    expect(container.querySelectorAll('table')).toHaveLength(0);
  });

  it('formats file sizes across the byte/KB/MB thresholds', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: {
          summary: 'ok',
          files: [
            { name: 'tiny.txt', url: 'https://x.test/tiny.txt', size: 500 },
            { name: 'mid.txt', url: 'https://x.test/mid.txt', size: 2048 },
            { name: 'big.txt', url: 'https://x.test/big.txt', size: 5 * 1024 * 1024 },
          ],
        } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('500B')).toBeInTheDocument();
    expect(screen.getByText('2KB')).toBeInTheDocument();
    expect(screen.getByText('5.0MB')).toBeInTheDocument();
  });

  it('derives a file name from its URL when no name/filename is given', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { summary: 'ok', files: [{ url: 'https://x.test/dir/report-final.pdf' }] } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('report-final.pdf')).toBeInTheDocument();
  });

  it('unwraps a summary that is a JSON-wrapped tool result', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { summary: '{"tool": "search", "result": "The real answer"}' } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('The real answer')).toBeInTheDocument();
  });

  it('leaves a summary starting with "{" but without a string result unchanged', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { summary: '{"result": 123}' } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText(/"result": 123/)).toBeInTheDocument();
  });

  it('leaves a malformed JSON-looking summary as-is rather than throwing', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { summary: '{"result": not valid json' } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText(/"result": not valid json/)).toBeInTheDocument();
  });

  it('falls back to rendering raw JSON when nothing readable is present', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { status: 'success', weird_field: 42 } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText(/"weird_field": 42/)).toBeInTheDocument();
  });

  it('labels multiple deliverables with an index (1/N, 2/N, ...)', () => {
    render(
      <MissionDeliverable
        outputs={['first one', 'second one']}
        evidence={[]}
      />,
    );
    expect(screen.getByText('Deliverable 1/2')).toBeInTheDocument();
    expect(screen.getByText('Deliverable 2/2')).toBeInTheDocument();
  });

  it('shows a failed status badge for a non-success deliverable', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: { status: 'error', summary: 'it broke' } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('error')).toBeInTheDocument();
  });

  it('renders the verification note and tool evidence chips', () => {
    render(
      <MissionDeliverable
        outputs={[{ deliverable: {
          summary: 'done',
          evidence: { verification: 'Checked against source of truth', tools: ['search', { name: 'calc' }] },
        } }]}
        evidence={[]}
      />,
    );
    expect(screen.getByText('Checked against source of truth')).toBeInTheDocument();
    expect(screen.getByText('search')).toBeInTheDocument();
    expect(screen.getByText('{"name":"calc"}')).toBeInTheDocument();
  });

  it('toggles the raw JSON view', async () => {
    render(<MissionDeliverable outputs={['plain text output']} evidence={[]} />);
    expect(screen.queryByText('"plain text output"')).not.toBeInTheDocument();
    await userEvent.click(screen.getByText('View raw JSON'));
    expect(screen.getByText('"plain text output"')).toBeInTheDocument();
    await userEvent.click(screen.getByText('Hide raw JSON'));
    expect(screen.queryByText('"plain text output"')).not.toBeInTheDocument();
  });

  describe('deliverable card actions', () => {
    afterEach(() => {
      vi.restoreAllMocks();
      vi.useRealTimers();
      // @ts-expect-error — test cleanup of a test-only global
      delete (navigator as unknown as { clipboard?: unknown }).clipboard;
      // @ts-expect-error — test cleanup of a test-only global
      delete (navigator as unknown as { share?: unknown }).share;
    });

    it('copies the deliverable body to the clipboard and shows a confirmation', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { clipboard: { writeText } });
      render(<MissionDeliverable outputs={['copy me']} evidence={[]} />);

      await userEvent.click(screen.getByLabelText('Copy result'));
      expect(writeText).toHaveBeenCalledWith('copy me');
    });

    it('falls back to stringified raw JSON on copy when there is no body text', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { clipboard: { writeText } });
      const raw = { deliverable: { metrics: [{ label: 'x', value: 1 }] } };
      render(<MissionDeliverable outputs={[raw]} evidence={[]} />);

      await userEvent.click(screen.getByLabelText('Copy result'));
      expect(writeText).toHaveBeenCalledWith(JSON.stringify(raw, null, 2));
    });

    it('swallows a clipboard failure on copy without crashing', async () => {
      const writeText = vi.fn().mockRejectedValue(new Error('denied'));
      Object.assign(navigator, { clipboard: { writeText } });
      render(<MissionDeliverable outputs={['copy me']} evidence={[]} />);
      await userEvent.click(screen.getByLabelText('Copy result'));
      expect(writeText).toHaveBeenCalled();
    });

    it('uses the native share sheet when available', async () => {
      const share = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { share });
      render(<MissionDeliverable outputs={[{ deliverable: { title: 'My deliverable', summary: 'share me' } }]} evidence={[]} />);
      await userEvent.click(screen.getByLabelText('Share result'));
      expect(share).toHaveBeenCalledWith({ title: 'My deliverable', text: 'share me' });
    });

    it('falls back to clipboard copy when native share throws', async () => {
      const share = vi.fn().mockRejectedValue(new Error('cancelled'));
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { share, clipboard: { writeText } });
      render(<MissionDeliverable outputs={['share me']} evidence={[]} />);
      await userEvent.click(screen.getByLabelText('Share result'));
      expect(writeText).toHaveBeenCalledWith('share me');
    });

    it('falls back to clipboard directly when no native share exists', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { clipboard: { writeText } });
      render(<MissionDeliverable outputs={['no native share']} evidence={[]} />);
      await userEvent.click(screen.getByLabelText('Share result'));
      expect(writeText).toHaveBeenCalledWith('no native share');
    });

    it('downloads the raw deliverable as JSON', async () => {
      const createObjectURL = vi.fn().mockReturnValue('blob:xyz');
      const revokeObjectURL = vi.fn();
      vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
      render(<MissionDeliverable outputs={['downloadable']} evidence={[]} />);
      await userEvent.click(screen.getByLabelText('Download deliverable as JSON'));
      expect(createObjectURL).toHaveBeenCalled();
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:xyz');
      vi.unstubAllGlobals();
    });
  });

  describe('publish receipt banner', () => {
    it('shows a failure banner with the connector error when publishing failed', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[]}
          published={{
            server_id: 'slack',
            tool_name: 'post_message',
            success: false,
            error: 'Rate limited',
            published_at: '2026-09-11T10:07:47Z',
          }}
        />,
      );
      expect(screen.getByText(/publish failed/i)).toBeInTheDocument();
      expect(screen.getByText('Rate limited')).toBeInTheDocument();
    });

    it('falls back to a generic error message when the connector gives none', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[]}
          published={{ server_id: 'slack', tool_name: 'post_message', success: false, published_at: '2026-09-11T10:07:47Z' }}
        />,
      );
      expect(screen.getByText('The connector returned an error.')).toBeInTheDocument();
    });

    it('finds a receipt link nested under output.body', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[]}
          published={{
            server_id: 'httpbin',
            tool_name: 'http_request',
            success: true,
            output: { body: { url: 'https://echo.test/body-url' } },
            published_at: '2026-09-11T10:07:47Z',
          }}
        />,
      );
      expect(screen.getByText('https://echo.test/body-url')).toBeInTheDocument();
    });

    it('renders no link when the receipt output carries none', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[]}
          published={{
            server_id: 'slack',
            tool_name: 'post_message',
            success: true,
            output: { ok: true },
            published_at: '2026-09-11T10:07:47Z',
          }}
        />,
      );
      expect(screen.getByText(/published/i)).toBeInTheDocument();
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
    });

    it('falls back to the raw published_at string when it cannot be parsed as a date', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[]}
          published={{ server_id: 's', tool_name: 't', success: true, published_at: 'not-a-real-date-string' }}
        />,
      );
      expect(screen.getByText(/Sent via s/)).toBeInTheDocument();
    });
  });

  describe('evidence strip', () => {
    it('renders a labelled object entry with a link', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[{ label: 'Source doc', href: 'https://src.test/a' }]}
        />,
      );
      const item = screen.getByText('https://src.test/a').closest('li')!;
      expect(within(item).getByText(/Source doc/)).toBeInTheDocument();
    });

    it('renders a labelled object entry without a link, using its value', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[{ label: 'Confidence', value: 0.92 }]}
        />,
      );
      expect(screen.getByText(/Confidence/)).toBeInTheDocument();
      expect(screen.getByText('0.92')).toBeInTheDocument();
    });

    it('serializes an object entry with an object value', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[{ type: 'metric', value: { nested: true } }]}
        />,
      );
      expect(screen.getByText('{"nested":true}')).toBeInTheDocument();
    });

    it('falls back to the whole object when there is no label or value', () => {
      render(
        <MissionDeliverable
          outputs={['x']}
          evidence={[{ random_key: 'random_value' }]}
        />,
      );
      expect(screen.getByText(/random_value/)).toBeInTheDocument();
    });

    it('renders a plain non-URL, non-object entry as text', () => {
      render(<MissionDeliverable outputs={['x']} evidence={[42]} />);
      expect(screen.getByText('42')).toBeInTheDocument();
    });
  });
});
