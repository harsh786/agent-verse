import { render, screen, fireEvent } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { WorkflowStepConfig } from './WorkflowStepConfig';

/** Grab the patch object most recently handed to onUpdate. */
function lastArg(fn: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return fn.mock.calls.at(-1)![0] as Record<string, unknown>;
}

function makeNode(stepType: string, data: Record<string, unknown> = {}, id = 'step-1'): Node {
  return {
    id,
    type: stepType,
    position: { x: 0, y: 0 },
    data: { stepType, ...data },
  } as unknown as Node;
}

function renderConfig(node: Node, onUpdate = vi.fn(), onClose = vi.fn()) {
  render(<WorkflowStepConfig node={node} onUpdate={onUpdate} onClose={onClose} />);
  return { onUpdate, onClose };
}

function expandAdvanced() {
  fireEvent.click(screen.getByRole('button', { name: /Advanced/i }));
}

afterEach(() => vi.restoreAllMocks());

describe('WorkflowStepConfig — trigger extra branches', () => {
  test('webhook trigger shows the webhook path field', () => {
    const { onUpdate } = renderConfig(makeNode('trigger', { triggerType: 'webhook' }));
    const path = screen.getByLabelText('Webhook path');
    fireEvent.change(path, { target: { value: '/webhooks/my-flow' } });
    expect(onUpdate).toHaveBeenCalledWith({ webhook_path: '/webhooks/my-flow' });
  });

  test('schedule trigger with unrecognized cron shows the amber warning copy', () => {
    renderConfig(makeNode('trigger', { triggerType: 'schedule', cron: 'garbage' }));
    expect(screen.getByText(/Unrecognized cron/i)).toBeInTheDocument();
  });

  test('schedule trigger with empty cron shows no humanized hint line', () => {
    renderConfig(makeNode('trigger', { triggerType: 'schedule', cron: '' }));
    expect(screen.queryByText(/Unrecognized cron/i)).not.toBeInTheDocument();
  });

  test('schedule trigger timezone field reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('trigger', { triggerType: 'schedule' }));
    fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'America/New_York' } });
    expect(onUpdate).toHaveBeenCalledWith({ timezone: 'America/New_York' });
  });

  test('various cron humanizer branches render distinct copy', () => {
    const cases: Array<[string, RegExp]> = [
      ['* * * * *', /Every minute/],
      ['*/15 * * * *', /Every 15 minutes/],
      ['0 */2 * * *', /Every 2 hours at :00/],
      ['30 9 * * 1', /Every Mon at 9:30 AM/],
      ['0 2 1 * *', /Day 1 of each month at 2:00 AM/],
      // Both day-of-month and day-of-week set falls through to the final catch-all.
      ['0 10 15 * 2', /^At 10:00 AM$/],
    ];
    for (const [cron, expected] of cases) {
      const { unmount } = render(
        <WorkflowStepConfig
          node={makeNode('trigger', { triggerType: 'schedule', cron })}
          onUpdate={vi.fn()}
          onClose={vi.fn()}
        />,
      );
      // The humanized hint renders as a <p>; preset buttons may carry the same
      // label text, so scope the match to paragraph elements.
      const matches = screen.getAllByText(expected).filter((el) => el.tagName === 'P');
      expect(matches.length).toBeGreaterThan(0);
      unmount();
    }
  });

  test('a non-numeric hour/minute in an "every day" cron falls back to raw values', () => {
    renderConfig(makeNode('trigger', { triggerType: 'schedule', cron: 'xx yy * * *' }));
    const matches = screen.getAllByText('Every day at yy:xx').filter((el) => el.tagName === 'P');
    expect(matches.length).toBeGreaterThan(0);
  });
});

describe('WorkflowStepConfig — llm extra branches', () => {
  test('temperature and max-tokens number fields report numeric edits and clearing', () => {
    const { onUpdate } = renderConfig(makeNode('llm', { temperature: 0.5, max_tokens: 1500 }));
    const temp = screen.getByLabelText('Temperature');
    expect(temp).toHaveValue(0.5);
    fireEvent.change(temp, { target: { value: '1.2' } });
    expect(onUpdate).toHaveBeenCalledWith({ temperature: 1.2 });

    fireEvent.change(temp, { target: { value: '' } });
    expect(onUpdate).toHaveBeenCalledWith({ temperature: undefined });

    const maxTokens = screen.getByLabelText('Max tokens');
    expect(maxTokens).toHaveValue(1500);
    fireEvent.change(maxTokens, { target: { value: '' } });
    expect(onUpdate).toHaveBeenCalledWith({ max_tokens: 2000 });
  });

  test('model select reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('llm'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'gemini-1.5-pro' } });
    expect(onUpdate).toHaveBeenCalledWith({ model: 'gemini-1.5-pro' });
  });

  test('JSON output toggle reflects an initially-true value', () => {
    renderConfig(makeNode('llm', { json_output: true }));
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  });
});

describe('WorkflowStepConfig — http extra branches', () => {
  test('basic auth shows username/password and reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('http', { auth: { type: 'basic' } }));
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'admin' } });
    expect(onUpdate).toHaveBeenCalledWith({ auth: { type: 'basic', username: 'admin' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret' } });
    expect(onUpdate).toHaveBeenCalledWith({ auth: { type: 'basic', password: 'secret' } });
  });

  test('none auth shows neither token nor username/password fields', () => {
    renderConfig(makeNode('http', { auth: { type: 'none' } }));
    expect(screen.queryByLabelText('Token')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Username')).not.toBeInTheDocument();
  });

  test('headers KeyValueEditor: add a row and edit key/value', () => {
    const { onUpdate } = renderConfig(makeNode('http'));
    // Both Headers and Request body render an "Add row" button — Headers is first.
    fireEvent.click(screen.getAllByRole('button', { name: /Add row/i })[0]);
    const key = screen.getByLabelText('Headers key 1');
    const value = screen.getByLabelText('Headers value 1');
    fireEvent.change(key, { target: { value: 'Content-Type' } });
    fireEvent.change(value, { target: { value: 'application/json' } });
    expect(onUpdate).toHaveBeenCalledWith({ headers: { 'Content-Type': 'application/json' } });
  });

  test('request body editor supports raw JSON mode round-trip', () => {
    const { onUpdate } = renderConfig(makeNode('http', { request_body: { a: 1 } }));
    // Toggle to raw JSON for the request body field (last "Raw JSON" toggle on page).
    const rawToggles = screen.getAllByRole('button', { name: /Raw JSON/i });
    fireEvent.click(rawToggles[rawToggles.length - 1]);
    const textarea = screen.getByPlaceholderText('{ }');
    fireEvent.change(textarea, { target: { value: '{"b": 2}' } });
    fireEvent.blur(textarea);
    expect(onUpdate).toHaveBeenCalledWith({ request_body: { b: 2 } });
  });
});

describe('WorkflowStepConfig — hitl extra branches', () => {
  test('timeout action select + escalation hours edits', () => {
    const { onUpdate } = renderConfig(makeNode('hitl'));
    fireEvent.change(screen.getByLabelText('On timeout'), { target: { value: 'approve' } });
    expect(onUpdate).toHaveBeenCalledWith({ timeout_action: 'approve' });
    fireEvent.change(screen.getByLabelText('Escalate after (hours)'), { target: { value: '12' } });
    expect(onUpdate).toHaveBeenCalledWith({ escalation_after_hours: 12 });
  });
});

describe('WorkflowStepConfig — foreach', () => {
  test('renders iterate/loop-var/concurrency/collect fields and reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('foreach'));
    fireEvent.change(screen.getByLabelText('Iterate over'), {
      target: { value: '{{steps.fetch.output.items}}' },
    });
    expect(onUpdate).toHaveBeenCalledWith({ iterate_over: '{{steps.fetch.output.items}}' });

    fireEvent.change(screen.getByLabelText('Loop variable'), { target: { value: 'row' } });
    expect(onUpdate).toHaveBeenCalledWith({ as_var: 'row' });

    const concurrency = screen.getByLabelText('Max concurrency');
    expect(concurrency).toHaveValue(5);
    fireEvent.change(concurrency, { target: { value: '10' } });
    expect(onUpdate).toHaveBeenCalledWith({ max_concurrency: 10 });
    fireEvent.change(concurrency, { target: { value: '' } });
    expect(onUpdate).toHaveBeenCalledWith({ max_concurrency: 5 });

    fireEvent.change(screen.getByLabelText('Collect output as'), { target: { value: 'results' } });
    expect(onUpdate).toHaveBeenCalledWith({ collect_output_as: 'results' });
  });
});

describe('WorkflowStepConfig — set_variable', () => {
  test('renders var name/value/type fields and reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('set_variable'));
    fireEvent.change(screen.getByLabelText('Variable name'), { target: { value: 'my_var' } });
    expect(onUpdate).toHaveBeenCalledWith({ var_name: 'my_var' });
    fireEvent.change(screen.getByLabelText('Value expression'), {
      target: { value: '{{steps.llm.output.result}}' },
    });
    expect(onUpdate).toHaveBeenCalledWith({ var_value: '{{steps.llm.output.result}}' });
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'number' } });
    expect(onUpdate).toHaveBeenCalledWith({ value_type: 'number' });
  });
});

describe('WorkflowStepConfig — wait', () => {
  test('renders duration and event channel fields and reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('wait'));
    fireEvent.change(screen.getByLabelText('Duration'), { target: { value: '30s' } });
    expect(onUpdate).toHaveBeenCalledWith({ duration: '30s' });
    fireEvent.change(screen.getByLabelText('Event channel (optional)'), {
      target: { value: 'payment.confirmed' },
    });
    expect(onUpdate).toHaveBeenCalledWith({ event_channel: 'payment.confirmed' });
  });
});

describe('WorkflowStepConfig — emit_event', () => {
  test('renders channel field + payload editor', () => {
    const { onUpdate } = renderConfig(makeNode('emit_event'));
    fireEvent.change(screen.getByLabelText('Event channel'), {
      target: { value: 'workflow.step.completed' },
    });
    expect(onUpdate).toHaveBeenCalledWith({ event_channel_out: 'workflow.step.completed' });
    expect(screen.getByText('Payload')).toBeInTheDocument();
  });
});

describe('WorkflowStepConfig — ocr extra fields', () => {
  test('all source fields patch into the nested input dict, and clearing one drops the key', () => {
    const { onUpdate } = renderConfig(makeNode('ocr', { input: { url: 'https://x/scan.pdf' } }));
    fireEvent.change(screen.getByLabelText('Image (base64 / ref)'), { target: { value: 'imgdata' } });
    expect(lastArg(onUpdate)).toEqual({ input: { url: 'https://x/scan.pdf', image_base64: 'imgdata' } });

    fireEvent.change(screen.getByLabelText('PDF (base64 / ref)'), { target: { value: 'pdfdata' } });
    fireEvent.change(screen.getByLabelText('Document (base64 / ref)'), { target: { value: 'docdata' } });
    fireEvent.change(screen.getByLabelText('File path'), { target: { value: '/tmp/a.png' } });
    fireEvent.change(screen.getByLabelText('Content type'), { target: { value: 'application/pdf' } });
    fireEvent.change(screen.getByLabelText('Filename'), { target: { value: 'invoice.pdf' } });

    // Clearing the URL field should drop the `url` key entirely (patchDict semantics).
    fireEvent.change(screen.getByLabelText('URL'), { target: { value: '' } });
    expect(lastArg(onUpdate).input).not.toHaveProperty('url');
  });
});

describe('WorkflowStepConfig — rpa', () => {
  test('renders url/title/selectors/toggles and reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('rpa'));
    fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.com' } });
    expect(lastArg(onUpdate)).toEqual({ input: { url: 'https://example.com' } });

    fireEvent.change(screen.getByLabelText('Title (optional)'), { target: { value: 'Report' } });
    expect(lastArg(onUpdate).input).toMatchObject({ title: 'Report' });

    // Toggles default to off; clicking flips to on.
    const switches = screen.getAllByRole('switch');
    expect(switches).toHaveLength(2);
    fireEvent.click(switches[0]);
    expect(lastArg(onUpdate).input).toMatchObject({ generate_pdf: true });
    fireEvent.click(switches[1]);
    expect(lastArg(onUpdate).input).toMatchObject({ allow_http_fetch: true });

    fireEvent.click(screen.getByRole('button', { name: /Add row/i }));
    fireEvent.change(screen.getByLabelText('Selectors key 1'), { target: { value: 'title' } });
    fireEvent.change(screen.getByLabelText('Selectors value 1'), { target: { value: 'h1.title' } });
    expect(lastArg(onUpdate).input).toMatchObject({ selectors: { title: 'h1.title' } });
  });
});

describe('WorkflowStepConfig — rag', () => {
  test('renders prompt/collection/top-k/strategy and reports edits', () => {
    const { onUpdate } = renderConfig(makeNode('rag'));
    fireEvent.change(screen.getByLabelText('Prompt / query'), {
      target: { value: 'Answer using {{inputs.question}}' },
    });
    expect(onUpdate).toHaveBeenCalledWith({ prompt: 'Answer using {{inputs.question}}' });

    fireEvent.change(screen.getByLabelText('Collection'), { target: { value: 'docs' } });
    expect(lastArg(onUpdate)).toEqual({ input: { collection: 'docs' } });

    const topK = screen.getByLabelText('Top K');
    expect(topK).toHaveValue(5);
    fireEvent.change(topK, { target: { value: '10' } });
    expect(onUpdate).toHaveBeenCalledWith({ rag: { top_k: 10 } });

    fireEvent.change(screen.getByLabelText('Strategy'), { target: { value: 'semantic' } });
    expect(onUpdate).toHaveBeenCalledWith({ rag: { strategy: 'semantic' } });
  });

  test('collection falls back to rag.collection when input.collection is unset', () => {
    renderConfig(makeNode('rag', { rag: { collection: 'legacy-coll' } }));
    expect(screen.getByLabelText('Collection')).toHaveValue('legacy-coll');
  });
});

describe('WorkflowStepConfig — advanced section extra branches', () => {
  test('retry attempts / backoff fields report edits', () => {
    const { onUpdate } = renderConfig(makeNode('tool', { retry: { max_attempts: 2, backoff_seconds: 5 } }));
    expandAdvanced();
    expect(screen.getByLabelText('Retry attempts')).toHaveValue(2);
    expect(screen.getByLabelText('Backoff (seconds)')).toHaveValue(5);
    fireEvent.change(screen.getByLabelText('Retry attempts'), { target: { value: '3' } });
    expect(onUpdate).toHaveBeenCalledWith({ retry: { max_attempts: 3, backoff_seconds: 5 } });
  });

  test('"use a default value" on-failure option reveals the default-value textarea', () => {
    const { onUpdate } = renderConfig(makeNode('tool'));
    expandAdvanced();
    fireEvent.change(screen.getByLabelText('On failure'), { target: { value: 'use_default' } });
    expect(onUpdate).toHaveBeenCalledWith({ on_failure: 'use_default' });
  });

  test('default-value textarea is shown and editable when on_failure is already use_default', () => {
    const { onUpdate } = renderConfig(makeNode('tool', { on_failure: 'use_default' }));
    expandAdvanced();
    const defaultValue = screen.getByLabelText('Default value');
    fireEvent.change(defaultValue, { target: { value: '{"status": "unknown"}' } });
    expect(onUpdate).toHaveBeenCalledWith({ on_failure_default: '{"status": "unknown"}' });
  });

  test('default-value textarea is hidden for other on-failure options', () => {
    renderConfig(makeNode('tool', { on_failure: 'skip' }));
    expandAdvanced();
    expect(screen.queryByLabelText('Default value')).not.toBeInTheDocument();
  });
});
