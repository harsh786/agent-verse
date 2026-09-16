/**
 * Broader CommandBar coverage: suggestion engine branches, keyboard
 * navigation, step transitions (input -> preview -> autonomy -> launch),
 * the estimate-success render path, and the useCommandBar Cmd+K hook.
 *
 * See __tests__/CommandBar.test.tsx for the FE1 "no fabricated numbers"
 * mission-preview regression tests — this file is a sibling covering the
 * remaining branches/functions in the same component.
 */
import { render, screen, fireEvent, renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';

import { CommandBar, useCommandBar } from './CommandBar';
import { orgApi } from './api';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => ReactNode>();
  const makeStub = (tag: string) => {
    // Memoized: the Proxy `get` trap below runs on every `motion.div` property
    // access (i.e. every render), so returning a fresh function each time would
    // give React a new component identity per render and force-remount the
    // whole subtree — detaching any DOM node references the test captured
    // earlier (e.g. an `<input>` element read via `getByLabelText` up front).
    if (!stubCache.has(tag)) {
      stubCache.set(tag, ({ children, ...props }) =>
        React.createElement(tag, props as Record<string, unknown>, children));
    }
    return stubCache.get(tag)!;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

const mutateAsync = vi.fn().mockResolvedValue({ id: 'm1' });
vi.mock('./hooks/useOrg', () => ({
  useCreateMission: () => ({ mutateAsync }),
}));

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mutateAsync.mockClear();
});
afterEach(() => vi.restoreAllMocks());

describe('CommandBar suggestion engine', () => {
  it('shows no suggestions for an empty query', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('suggests a mission for "launch"/"product" text', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'launch new product' } });
    expect(screen.getByText(/Create mission: "launch new product"/)).toBeInTheDocument();
  });

  it('suggests a research mission for "research"/"analyze" text', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'analyze the market' } });
    expect(screen.getByText(/Start research mission: "analyze the market"/)).toBeInTheDocument();
  });

  it('suggests asking the org for "status"/"what" text', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'what is the status' } });
    expect(screen.getByText(/Ask org: "what is the status"/)).toBeInTheDocument();
  });

  it('falls back to generic create/search suggestions for unmatched text', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'foo bar baz' } });
    expect(screen.getByText(/Create mission: "foo bar baz"/)).toBeInTheDocument();
    expect(screen.getByText(/Search: "foo bar baz"/)).toBeInTheDocument();
  });

  it('clears the query via the clear button', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    const input = screen.getByLabelText('Command input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'hello' } });
    fireEvent.click(screen.getByLabelText('Clear input'));
    expect(input.value).toBe('');
  });
});

describe('CommandBar keyboard interaction', () => {
  it('navigates suggestions with ArrowDown/ArrowUp', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    const input = screen.getByLabelText('Command input');
    fireEvent.change(input, { target: { value: 'foo bar baz' } });
    const options = screen.getAllByRole('option');
    expect(options[0]).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(options[1]).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('does not go below the first suggestion on ArrowUp', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    const input = screen.getByLabelText('Command input');
    fireEvent.change(input, { target: { value: 'foo bar baz' } });
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('submits on Enter and moves to the preview step', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    const input = screen.getByLabelText('Command input');
    fireEvent.change(input, { target: { value: 'launch product' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByText('Mission Preview')).toBeInTheDocument();
  });

  it('Enter with an empty query does not submit', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    const input = screen.getByLabelText('Command input');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.queryByText('Mission Preview')).not.toBeInTheDocument();
  });

  it('closes on Escape key from the input step', () => {
    const onClose = vi.fn();
    wrap(<CommandBar orgId="org-1" onClose={onClose} />);
    fireEvent.keyDown(screen.getByLabelText('Command input'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('closes on a global window Escape keydown', () => {
    const onClose = vi.fn();
    wrap(<CommandBar orgId="org-1" onClose={onClose} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('closes when the backdrop is clicked', () => {
    const onClose = vi.fn();
    const { container } = wrap(<CommandBar orgId="org-1" onClose={onClose} />);
    fireEvent.click(container.querySelector('[role="dialog"]')!);
    expect(onClose).toHaveBeenCalled();
  });
});

describe('CommandBar step transitions and mission launch', () => {
  beforeEach(() => {
    // The preview step fires a real (unmocked) previewMission() call whose
    // .finally() clears isLoading — which the Launch Mission button is
    // disabled on. Resolve it immediately so isLoading clears synchronously
    // with the rest of these tests instead of leaving it stuck on a pending
    // real network call that never settles in jsdom.
    vi.spyOn(orgApi, 'previewMission').mockResolvedValue({
      departments: [], estimated_agents: 1, estimated_duration_hours: 1,
      estimated_cost_usd: 1, estimated_risk: 'low', confidence: 1,
      success_probability: 1, potential_blockers: [],
    });
  });

  it('goes back from preview to input', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'launch it' } });
    fireEvent.click(screen.getAllByRole('option')[0]);
    expect(screen.getByText('Mission Preview')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByLabelText('Command input')).toBeInTheDocument();
  });

  it('continues from preview to the autonomy step', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'launch it' } });
    fireEvent.click(screen.getAllByRole('option')[0]);
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }));
    expect(screen.getByText('Autonomy Level')).toBeInTheDocument();
    expect(screen.getByText('L3 Standard')).toBeInTheDocument();
  });

  it('goes back from autonomy to preview', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'launch it' } });
    fireEvent.click(screen.getAllByRole('option')[0]);
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByText('Mission Preview')).toBeInTheDocument();
  });

  it('changing the autonomy slider updates the label and level', () => {
    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'launch it' } });
    fireEvent.click(screen.getAllByRole('option')[0]);
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }));

    // jsdom doesn't implement a native <input type="range">'s built-in
    // arrow-key stepping, so drive it the way a real drag/keypress would
    // ultimately manifest: an onChange with the new value.
    fireEvent.change(screen.getByLabelText('Autonomy level'), { target: { value: '4' } });
    expect(screen.getByText('L4 Autonomous')).toBeInTheDocument();
  });

  it('launching the mission calls createMission and closes the modal', async () => {
    const onClose = vi.fn();
    wrap(<CommandBar orgId="org-42" onClose={onClose} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'launch it' } });
    fireEvent.click(screen.getAllByRole('option')[0]);
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }));

    // The preview step's previewMission() call clears isLoading (which the
    // Launch Mission button is disabled on) only after its promise's
    // microtask flushes — wait for that before clicking, rather than
    // racing a still-disabled button.
    await waitFor(() => expect(screen.getByRole('button', { name: /Launch Mission/ })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: /Launch Mission/ }));

    await waitFor(() => expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ org_id: 'org-42', title: 'launch it', priority: 'medium', autonomy_level: 3 }),
    ));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});

describe('CommandBar mission preview with a resolved estimate', () => {
  it('renders the estimate grid, departments, risk, and blockers once loaded', async () => {
    vi.spyOn(orgApi, 'previewMission').mockResolvedValue({
      departments: ['engineering', 'marketing'],
      estimated_agents: 5,
      estimated_duration_hours: 12,
      estimated_cost_usd: 42.5,
      estimated_risk: 'high',
      confidence: 0.8,
      success_probability: 0.7,
      potential_blockers: ['Needs legal sign-off'],
    });

    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'launch it' } });
    fireEvent.click(screen.getAllByRole('option')[0]);

    await waitFor(() => expect(screen.getByText('5')).toBeInTheDocument());
    expect(screen.getByText('$42.50')).toBeInTheDocument();
    expect(screen.getByText('12h')).toBeInTheDocument();
    expect(screen.getByText('engineering')).toBeInTheDocument();
    expect(screen.getByText('marketing')).toBeInTheDocument();
    expect(screen.getByText('high risk')).toBeInTheDocument();
    expect(screen.getByText('Needs legal sign-off')).toBeInTheDocument();
  });

  it('shows medium and low risk colors distinctly (no blockers list when empty)', async () => {
    vi.spyOn(orgApi, 'previewMission').mockResolvedValue({
      departments: ['ops'],
      estimated_agents: 2,
      estimated_duration_hours: 3,
      estimated_cost_usd: 5,
      estimated_risk: 'low',
      confidence: 0.9,
      success_probability: 0.9,
      potential_blockers: [],
    });

    wrap(<CommandBar orgId="org-1" onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Command input'), { target: { value: 'launch it' } });
    fireEvent.click(screen.getAllByRole('option')[0]);

    await waitFor(() => expect(screen.getByText('low risk')).toBeInTheDocument());
  });
});

describe('useCommandBar hook', () => {
  it('toggles isOpen on Cmd+K / Ctrl+K and exposes open/close', () => {
    const { result } = renderHook(() => useCommandBar());
    expect(result.current.isOpen).toBe(false);

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }));
    });
    expect(result.current.isOpen).toBe(true);

    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }));
    });
    expect(result.current.isOpen).toBe(false);

    act(() => result.current.open());
    expect(result.current.isOpen).toBe(true);

    act(() => result.current.close());
    expect(result.current.isOpen).toBe(false);
  });
});
