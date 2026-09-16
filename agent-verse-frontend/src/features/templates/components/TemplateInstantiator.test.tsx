/**
 * TemplateInstantiator tests.
 *
 * A modal that interpolates {{param}} placeholders into a goal preview and,
 * depending on props, either submits the goal (Run Now → POST
 * /templates/:id/instantiate) or hands the text back via onUseInGoal. Covers
 * preview interpolation, required-param gating, both submit paths, copy, and the
 * no-parameter branch.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { GoalTemplate } from '@/lib/api/client';
import { TemplateInstantiator } from './TemplateInstantiator';

function template(overrides: Partial<GoalTemplate> = {}): GoalTemplate {
  return {
    id: 'tpl-1',
    name: 'Deploy Service',
    description: 'Deploys a service to an environment.',
    goal_text: 'Deploy {{service}} to production',
    domain: 'devops',
    parameters: [{ name: 'service', description: 'Service name', required: true }],
    use_count: 3,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderModal(props: Partial<React.ComponentProps<typeof TemplateInstantiator>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = props.onClose ?? vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TemplateInstantiator template={template()} onClose={onClose} {...props} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { onClose };
}

describe('TemplateInstantiator', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  test('renders the template name, the parameter input, and the initial preview', () => {
    renderModal();
    expect(screen.getByRole('heading', { name: 'Deploy Service' })).toBeInTheDocument();
    expect(screen.getByLabelText(/service/i)).toBeInTheDocument();
    // Preview keeps the placeholder until a value is supplied.
    expect(screen.getByText('Deploy {{service}} to production')).toBeInTheDocument();
  });

  test('preview interpolates the parameter value as the user types', async () => {
    renderModal();
    await userEvent.type(screen.getByLabelText(/service/i), 'billing-api');
    expect(screen.getByText('Deploy billing-api to production')).toBeInTheDocument();
  });

  test('no-parameter templates show the as-is message', () => {
    renderModal({ template: template({ parameters: [], goal_text: 'Run nightly backup' }) });
    expect(screen.getByText(/This template has no parameters/i)).toBeInTheDocument();
  });

  test('Run Now is gated on required params then POSTs an instantiate+submit', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({
        template_id: 'tpl-1',
        instantiated_goal: 'Deploy billing-api to production',
        parameters_used: { service: 'billing-api' },
        submitted_goal: { goal_id: 'goal-9', status: 'queued' },
      })
    );
    renderModal();
    const runBtn = screen.getByRole('button', { name: /Run Now/i });
    expect(runBtn).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/service/i), 'billing-api');
    expect(runBtn).toBeEnabled();
    await userEvent.click(runBtn);

    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            /\/templates\/tpl-1\/instantiate$/.test(String(u)) && (i as RequestInit)?.method === 'POST'
        )
      ).toBe(true)
    );
  });

  test('with onUseInGoal it shows "Use in Goal" and hands back the interpolated text', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ template_id: 'tpl-1', instantiated_goal: '', parameters_used: {} }));
    const onUseInGoal = vi.fn();
    const onClose = vi.fn();
    renderModal({ onUseInGoal, onClose });

    expect(screen.queryByRole('button', { name: /Run Now/i })).not.toBeInTheDocument();
    const useBtn = screen.getByRole('button', { name: /Use in Goal/i });
    expect(useBtn).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/service/i), 'billing-api');
    expect(useBtn).toBeEnabled();
    await userEvent.click(useBtn);

    expect(onUseInGoal).toHaveBeenCalledWith('Deploy billing-api to production');
    expect(onClose).toHaveBeenCalled();
  });

  test('copy writes the interpolated goal text to the clipboard', async () => {
    renderModal();
    await userEvent.type(screen.getByLabelText(/service/i), 'billing-api');
    await userEvent.click(screen.getByRole('button', { name: /^Copy$/i }));
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('Deploy billing-api to production')
    );
  });

  test('Cancel invokes onClose', async () => {
    const { onClose } = renderModal();
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(onClose).toHaveBeenCalled();
  });

  test('enum parameters render a select of options', () => {
    renderModal({
      template: template({
        goal_text: 'Deploy to {{env}}',
        parameters: [{ name: 'env', description: 'Environment', required: true, enum: ['staging', 'prod'] } as never],
      }),
    });
    const select = screen.getByLabelText(/env/i);
    expect(select.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: 'staging' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'prod' })).toBeInTheDocument();
  });
});
