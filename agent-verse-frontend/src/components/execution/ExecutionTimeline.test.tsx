import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
});
