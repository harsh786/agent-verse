import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PatternSelectionResponse } from '@/lib/api/client';
import { PatternSelectionPanel } from './PatternSelectionPanel';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', () => ({ useNavigate: () => navigateSpy }));
vi.mock('@/stores/toast', () => ({ toast: vi.fn() }));

const getPatternSelection = vi.fn();
const submit = vi.fn();
vi.mock('@/lib/api/client', () => ({
  goalsApi: {
    getPatternSelection: (id: string) => getPatternSelection(id),
    submit: (body: unknown) => submit(body),
  },
}));

const SELECTION: PatternSelectionResponse = {
  goal_id: 'g1',
  status: 'completed',
  source: 'auto',
  override: null,
  primary_pattern: 'react',
  primary_pattern_name: 'ReAct',
  reasoning_patterns: ['react', 'reflection'],
  multi_agent_patterns: ['supervisor'],
  safety_patterns: ['guardrails'],
  autonomy_mode: 'bounded-autonomous',
  max_iterations: 25,
  advanced_tier_enabled: false,
  advanced_tier_gated: true,
  goal_properties: {
    complexity: 'complex',
    domain: 'technical',
    risk: 'low',
    multi_step: true,
    requires_code: false,
    classifier_confidence: 0.9,
  },
  rationale: [
    { pattern: 'react', name: 'ReAct', category: 'reasoning', why: 'default reasoning loop' },
    {
      pattern: 'supervisor',
      name: 'Supervisor',
      category: 'multi_agent',
      why: 'complexity=complex multi_step domain=technical',
    },
  ],
  available_patterns: [
    { id: 'react', name: 'ReAct', description: 'ReAct loop', state: 'implemented', available: true, cost_class: 'medium', latency_class: 'interactive' },
    { id: 'debate', name: 'Debate', description: 'Agents debate', state: 'implemented', available: true, cost_class: 'high', latency_class: 'batch' },
  ],
};

const wrapper = () => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
};

describe('PatternSelectionPanel', () => {
  beforeEach(() => {
    getPatternSelection.mockReset().mockResolvedValue(SELECTION);
    submit.mockReset().mockResolvedValue({ goal_id: 'g2', id: 'g2' });
    navigateSpy.mockReset();
  });

  it('renders the real auto-selected pattern and its rationale', async () => {
    render(<PatternSelectionPanel goalId="g1" goalText="do a thing" />, { wrapper: wrapper() });
    expect(await screen.findByTestId('pattern-primary-name')).toHaveTextContent('ReAct');
    expect(screen.getByText('Auto-selected')).toBeInTheDocument();
    // Plain-language rationale, both categories.
    const rationale = screen.getByTestId('pattern-rationale');
    expect(rationale).toHaveTextContent('default reasoning loop');
    expect(rationale).toHaveTextContent('complexity=complex multi_step domain=technical');
  });

  it('shows the gated-tier notice when an advanced pattern is selected but gated', async () => {
    render(<PatternSelectionPanel goalId="g1" goalText="do a thing" />, { wrapper: wrapper() });
    expect(await screen.findByTestId('pattern-gated-note')).toBeInTheDocument();
  });

  it('overrides: re-submits the goal with the chosen strategy and navigates', async () => {
    render(<PatternSelectionPanel goalId="g1" goalText="do a thing" />, { wrapper: wrapper() });
    await screen.findByTestId('pattern-primary-name');

    fireEvent.change(screen.getByTestId('pattern-override-select'), {
      target: { value: 'debate' },
    });
    fireEvent.click(screen.getByTestId('pattern-override-run'));

    await waitFor(() => {
      expect(submit).toHaveBeenCalledWith(
        expect.objectContaining({ goal: 'do a thing', strategy_override: 'debate' }),
      );
    });
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/goals/g2'));
  });

  it('shows an empty state when the request fails', async () => {
    getPatternSelection.mockRejectedValueOnce(new Error('boom'));
    render(<PatternSelectionPanel goalId="g1" />, { wrapper: wrapper() });
    expect(await screen.findByTestId('pattern-empty')).toBeInTheDocument();
  });
});
