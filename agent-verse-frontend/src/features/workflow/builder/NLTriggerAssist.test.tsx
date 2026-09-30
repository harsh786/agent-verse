/**
 * WF-09: the natural-language trigger helper applies the parsed trigger and,
 * on a 422, shows the server's reason plus phrasings that work.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: { nlTriggerPreview: vi.fn() },
}));

import { workflowEngineApi } from '../../../lib/api/client';
import { NLTriggerAssist } from './NLTriggerAssist';

afterEach(() => vi.clearAllMocks());

function describeTrigger(text: string) {
  fireEvent.change(screen.getByLabelText('Or describe it'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: /apply/i }));
}

describe('NLTriggerAssist', () => {
  test('applies a parsed schedule', async () => {
    vi.mocked(workflowEngineApi.nlTriggerPreview).mockResolvedValue({
      trigger: { type: 'schedule', schedule: { cron: '30 7 * * 2' } },
      description: 'x',
    });
    const onApply = vi.fn();
    render(<NLTriggerAssist onApply={onApply} />);
    describeTrigger('on tuesdays at half past seven');

    expect(await screen.findByText('Applied: Schedule · 30 7 * * 2')).toBeInTheDocument();
    expect(workflowEngineApi.nlTriggerPreview).toHaveBeenCalledWith('on tuesdays at half past seven');
    expect(onApply).toHaveBeenCalledWith({ triggerType: 'schedule', cron: '30 7 * * 2' });
  });

  test('shows the 422 detail and example phrasings instead of a generic error', async () => {
    vi.mocked(workflowEngineApi.nlTriggerPreview).mockRejectedValue(
      Object.assign(new Error('Could not understand this trigger description'), { status: 422 }),
    );
    const onApply = vi.fn();
    render(<NLTriggerAssist onApply={onApply} />);
    describeTrigger('when the moon is full');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not understand this trigger description');
    expect(alert).toHaveTextContent('every day at midnight');
    expect(onApply).not.toHaveBeenCalled();
  });

  test('does not apply a trigger type that cannot fire', async () => {
    vi.mocked(workflowEngineApi.nlTriggerPreview).mockResolvedValue({
      trigger: { type: 'event' },
      description: 'x',
    });
    const onApply = vi.fn();
    render(<NLTriggerAssist onApply={onApply} />);
    describeTrigger('when an order event arrives');

    expect(await screen.findByRole('alert')).toHaveTextContent(/can't start runs yet/i);
    expect(onApply).not.toHaveBeenCalled();
  });
});
