import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ExecutionTimeline } from './ExecutionTimeline';

function setViewport(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    value: width,
  });
  Object.defineProperty(window, 'innerHeight', {
    configurable: true,
    value: height,
  });
}

function rect(overrides: Partial<DOMRect>): DOMRect {
  return {
    bottom: 140,
    height: 40,
    left: 100,
    right: 140,
    top: 100,
    width: 40,
    x: 100,
    y: 100,
    toJSON: () => ({}),
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  setViewport(1024, 768);
});

describe('ExecutionTimeline', () => {
  test('timeline items expose accessible tooltip labels for tool events', () => {
    render(
      <ExecutionTimeline
        events={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            success: true,
          },
        ]}
      />
    );

    const toolEvent = screen.getByRole('button', {
      name: /tool call complete jira_search_issues/i,
    });

    expect(toolEvent).toBeInTheDocument();
    expect(toolEvent).toHaveAttribute('type', 'button');
  });

  test('shows tooltip on hover and focus without keeping it permanently visible', async () => {
    const user = userEvent.setup();

    render(
      <ExecutionTimeline
        events={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            success: true,
          },
        ]}
      />
    );

    const toolEvent = screen.getByRole('button', {
      name: /tool call complete jira_search_issues/i,
    });

    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

    await user.hover(toolEvent);
    const hoveredTooltip = screen.getByRole('tooltip');
    expect(hoveredTooltip).toHaveTextContent('Tool: jira_search_issues');
    expect(hoveredTooltip).toHaveClass('fixed');

    await user.unhover(toolEvent);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

    await user.tab();
    const focusedTooltip = screen.getByRole('tooltip');
    expect(focusedTooltip).toHaveTextContent('Tool: jira_search_issues');
    expect(toolEvent).toHaveAttribute('aria-describedby', focusedTooltip.id);

    await user.tab();
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  test('keeps tooltip visible when a focused item loses hover', async () => {
    const user = userEvent.setup();

    render(
      <ExecutionTimeline
        events={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            success: true,
          },
        ]}
      />
    );

    const toolEvent = screen.getByRole('button', {
      name: /tool call complete jira_search_issues/i,
    });

    await user.hover(toolEvent);
    await user.click(toolEvent);
    await user.unhover(toolEvent);

    expect(screen.getByRole('tooltip')).toHaveTextContent('Tool: jira_search_issues');
  });

  test('dismisses active tooltip with Escape', async () => {
    const user = userEvent.setup();

    render(
      <ExecutionTimeline
        events={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            success: true,
          },
        ]}
      />
    );

    const toolEvent = screen.getByRole('button', {
      name: /tool call complete jira_search_issues/i,
    });

    await user.hover(toolEvent);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  test('keeps tooltip within narrow viewport and flips below top edge triggers', async () => {
    const user = userEvent.setup();
    setViewport(320, 240);

    render(
      <ExecutionTimeline
        events={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            success: true,
          },
        ]}
      />
    );

    const toolEvent = screen.getByRole('button', {
      name: /tool call complete jira_search_issues/i,
    });
    vi.spyOn(toolEvent, 'getBoundingClientRect').mockReturnValue(
      rect({ bottom: 44, left: 4, right: 44, top: 4, x: 4, y: 4 })
    );

    await user.hover(toolEvent);

    const tooltip = screen.getByRole('tooltip');
    expect(tooltip).toHaveClass('w-64');
    expect(tooltip).toHaveClass('max-w-[calc(100vw-1.5rem)]');
    expect(parseFloat(tooltip.style.top)).toBeGreaterThan(44);
    expect(parseFloat(tooltip.style.left)).toBeGreaterThanOrEqual(12);
    expect(parseFloat(tooltip.style.left)).toBeLessThanOrEqual(308);
  });

  test('updates tooltip position on viewport resize while active', async () => {
    const user = userEvent.setup();
    setViewport(800, 600);

    render(
      <ExecutionTimeline
        events={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            success: true,
          },
        ]}
      />
    );

    const toolEvent = screen.getByRole('button', {
      name: /tool call complete jira_search_issues/i,
    });
    vi.spyOn(toolEvent, 'getBoundingClientRect')
      .mockReturnValueOnce(rect({ left: 120, right: 160, x: 120 }))
      .mockReturnValueOnce(rect({ left: 120, right: 160, x: 120 }))
      .mockReturnValue(rect({ left: 260, right: 300, x: 260 }));

    await user.hover(toolEvent);
    expect(parseFloat(screen.getByRole('tooltip').style.left)).toBe(140);

    fireEvent.resize(window);

    await waitFor(() => {
      expect(parseFloat(screen.getByRole('tooltip').style.left)).toBe(280);
    });
  });

  test('renders nothing when there are no events', () => {
    const { container } = render(<ExecutionTimeline events={[]} />);

    expect(container.firstChild).toBeNull();
  });

  test('renders only the first 40 events and shows a count for the remainder', () => {
    const events = Array.from({ length: 45 }, (_, i) => ({
      type: 'step_started',
      step: `step-${i}`,
    }));

    render(<ExecutionTimeline events={events} />);

    expect(screen.getAllByRole('button')).toHaveLength(40);
    expect(screen.getByText('+5 more')).toBeInTheDocument();
    expect(screen.getByText(/45 events/)).toBeInTheDocument();
  });

  test('renders a connector line between consecutive events and dismisses a tooltip whose trigger unmounts', async () => {
    const user = userEvent.setup();
    const events = [
      { type: 'tool_call_complete', tool_name: 'first', success: true },
      { type: 'tool_call_complete', tool_name: 'second', success: true },
    ];

    const { container, rerender } = render(<ExecutionTimeline events={events} />);

    expect(container.querySelectorAll('.flex-shrink-0.bg-border')).toHaveLength(1);

    const buttons = screen.getAllByRole('button');
    await user.hover(buttons[1]);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    rerender(<ExecutionTimeline events={[events[0]]} />);
    fireEvent.resize(window);

    await waitFor(() => {
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    });
  });

  test('positions tooltip above the trigger when there is enough room', async () => {
    const user = userEvent.setup();

    render(
      <ExecutionTimeline
        events={[{ type: 'tool_call_complete', tool_name: 'jira_search_issues', success: true }]}
      />
    );

    const toolEvent = screen.getByRole('button');
    vi.spyOn(toolEvent, 'getBoundingClientRect').mockReturnValue(
      rect({ top: 300, bottom: 340, left: 500, right: 540, x: 500, y: 300 })
    );

    await user.hover(toolEvent);

    const tooltip = screen.getByRole('tooltip');
    expect(parseFloat(tooltip.style.top)).toBeLessThan(300);
  });

  test('falls back to a clamped position when neither above nor below the trigger fits', async () => {
    const user = userEvent.setup();
    setViewport(320, 100);

    render(
      <ExecutionTimeline
        events={[{ type: 'tool_call_complete', tool_name: 'jira_search_issues', success: true }]}
      />
    );

    const toolEvent = screen.getByRole('button');
    vi.spyOn(toolEvent, 'getBoundingClientRect').mockReturnValue(
      rect({ top: 50, bottom: 90, left: 100, right: 140, x: 100, y: 100 })
    );

    await user.hover(toolEvent);

    const tooltip = screen.getByRole('tooltip');
    expect(parseFloat(tooltip.style.top)).toBe(12);
  });

  test('falls back to "Tool call" label when a tool event has no tool_name', () => {
    render(<ExecutionTimeline events={[{ type: 'tool_call_complete', success: true }]} />);

    expect(
      screen.getByRole('button', { name: /tool call complete tool call/i })
    ).toBeInTheDocument();
  });

  test('truncates long step labels and falls back when step is missing', () => {
    const longStep = 'a'.repeat(30);

    render(
      <ExecutionTimeline
        events={[{ type: 'step_started', step: longStep }, { type: 'step_complete' }]}
      />
    );

    const buttons = screen.getAllByRole('button');
    expect(buttons[0]).toHaveAccessibleName(`step started ${longStep.slice(0, 24)}`);
    expect(buttons[1]).toHaveAccessibleName('step complete step complete');
  });

  test('tooltip omits optional fields that are absent from the event', async () => {
    const user = userEvent.setup();

    render(<ExecutionTimeline events={[{ type: 'plan_ready' }]} />);

    await user.hover(screen.getByRole('button'));

    const tooltip = screen.getByRole('tooltip');
    expect(tooltip).toHaveTextContent('plan ready');
    expect(within(tooltip).queryByText(/Step:/)).not.toBeInTheDocument();
    expect(within(tooltip).queryByText(/Tool:/)).not.toBeInTheDocument();
  });

  test('tooltip shows step, tool, and timestamp fields when present', async () => {
    const user = userEvent.setup();

    render(
      <ExecutionTimeline
        events={[
          {
            type: 'tool_call_complete',
            tool_name: 'jira_search_issues',
            step: 'Search issues',
            ts: '2024-01-01T12:34:56.000Z',
            success: true,
          },
        ]}
      />
    );

    await user.hover(screen.getByRole('button'));

    const tooltip = screen.getByRole('tooltip');
    expect(tooltip).toHaveTextContent('Step: Search issues');
    expect(tooltip).toHaveTextContent('Tool: jira_search_issues');
    expect(tooltip).toHaveTextContent('2024-01-01T12:34:56');
  });

  test.each([
    ['goal_complete', undefined, 'bg-green-500'],
    ['step_complete', undefined, 'bg-green-500'],
    ['step_started', true, 'bg-green-500'],
    ['goal_failed', undefined, 'bg-red-500'],
    ['tool_call_failed', undefined, 'bg-red-500'],
    ['tool_call_complete', false, 'bg-red-500'],
    ['tool_call_complete', undefined, 'bg-blue-500'],
    ['plan_ready', undefined, 'bg-purple-500'],
    ['step_started', undefined, 'bg-yellow-500'],
    ['goal_started', undefined, 'bg-yellow-500'],
    ['unrecognized_event', undefined, 'bg-gray-400'],
  ])('colors "%s" event (success=%s) as %s', (type, success, expectedClass) => {
    render(<ExecutionTimeline events={[{ type, success }]} />);

    const button = screen.getByRole('button');
    const swatch = button.querySelector('div');
    expect(swatch).toHaveClass(expectedClass);
  });
});
