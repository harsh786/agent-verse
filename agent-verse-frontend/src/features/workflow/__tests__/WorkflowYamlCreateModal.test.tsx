import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { WorkflowYamlCreateModal } from '../builder/WorkflowYamlCreateModal';
import { workflowEngineApi } from '../../../lib/api/client';

vi.mock('../../../lib/api/client', () => ({
  workflowEngineApi: { create: vi.fn() },
}));

function renderModal(onCreated = vi.fn(), onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <WorkflowYamlCreateModal onClose={onClose} onCreated={onCreated} />
    </QueryClientProvider>,
  );
  return { onCreated, onClose };
}

describe('WorkflowYamlCreateModal', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows a valid step count for the default template', () => {
    renderModal();
    expect(screen.getByText(/Valid · 3 steps/i)).toBeInTheDocument();
  });

  it('flags invalid YAML and disables create', () => {
    renderModal();
    const ta = screen.getByLabelText('Workflow YAML') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'name: "unterminated' } });
    expect(screen.getByRole('button', { name: /Create workflow/i })).toBeDisabled();
  });

  it('requires a name', () => {
    renderModal();
    const ta = screen.getByLabelText('Workflow YAML') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'steps: []' } });
    expect(screen.getByText(/name.*required/i)).toBeInTheDocument();
  });

  it('creates a workflow from parsed YAML and calls onCreated', async () => {
    (workflowEngineApi.create as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'wf-99' });
    const { onCreated } = renderModal();
    const ta = screen.getByLabelText('Workflow YAML') as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: 'name: My Flow\nsteps:\n  - id: s1\n    type: llm' } });
    fireEvent.click(screen.getByRole('button', { name: /Create workflow/i }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('wf-99'));
    expect(workflowEngineApi.create).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'My Flow' }),
    );
  });

  it('switches templates', () => {
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: /RAG knowledge Q&A/i }));
    const ta = screen.getByLabelText('Workflow YAML') as HTMLTextAreaElement;
    expect(ta.value).toContain('name: Knowledge Q&A');
  });
});
