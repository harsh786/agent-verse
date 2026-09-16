import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { ToolSparkNode } from './ToolSparkNode';

// Force motion ON so the spark-burst effect runs deterministically.
const motionState = vi.hoisted(() => ({ reduced: false }));
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  return { ...actual, useReducedMotion: () => motionState.reduced };
});

afterEach(() => {
  motionState.reduced = false;
});

// Spark elements are the only `.rounded-full` nodes; the core node is rounded-lg.
const sparkCount = (c: HTMLElement) => c.querySelectorAll('.rounded-full').length;

describe('ToolSparkNode', () => {
  test('renders the tool name', () => {
    render(<ToolSparkNode toolName="search_web" state="idle" />);
    expect(screen.getByText('search_web')).toBeInTheDocument();
  });

  test('truncates a very long tool name to 30 characters', () => {
    const long = 'a_really_long_tool_name_exceeding_limit';
    render(<ToolSparkNode toolName={long} state="idle" />);
    expect(screen.getByText(long.slice(0, 30))).toBeInTheDocument();
    expect(screen.queryByText(long)).not.toBeInTheDocument();
  });

  test('emits no sparks while idle', () => {
    const { container } = render(<ToolSparkNode toolName="noop" state="idle" />);
    expect(sparkCount(container)).toBe(0);
  });

  test('bursts 8 sparks on success and 4 on failure', () => {
    const { container: ok } = render(<ToolSparkNode toolName="deploy" state="success" />);
    expect(sparkCount(ok)).toBe(8);

    const { container: bad } = render(<ToolSparkNode toolName="deploy" state="failed" />);
    expect(sparkCount(bad)).toBe(4);
  });

  test('reflects the state color on the tool label', () => {
    render(<ToolSparkNode toolName="calling_tool" state="calling" />);
    // #FFB300 → rgb(255, 179, 0) once jsdom normalizes the inline style
    expect(screen.getByText('calling_tool')).toHaveStyle({ color: 'rgb(255, 179, 0)' });
  });
});
