import { render, screen } from '@testing-library/react';
import { describe, expect, test } from 'vitest';
import {
  WorkflowListSkeleton,
  WorkflowCanvasSkeleton,
  RunTimelineSkeleton,
  RunStatsSkeleton,
} from './Skeletons';

describe('workflow skeletons', () => {
  test('WorkflowListSkeleton is an accessible loading status', () => {
    render(<WorkflowListSkeleton />);
    expect(screen.getByRole('status', { name: /Loading workflows/i })).toBeInTheDocument();
    // sr-only live-region text for screen readers
    expect(screen.getByText('Loading workflows…')).toBeInTheDocument();
  });

  test('WorkflowCanvasSkeleton announces the canvas is loading', () => {
    render(<WorkflowCanvasSkeleton />);
    expect(screen.getByRole('status', { name: /Loading workflow canvas/i })).toBeInTheDocument();
    expect(screen.getByText('Loading canvas…')).toBeInTheDocument();
  });

  test('RunTimelineSkeleton announces the timeline is loading', () => {
    render(<RunTimelineSkeleton />);
    expect(screen.getByRole('status', { name: /Loading run timeline/i })).toBeInTheDocument();
    expect(screen.getByText('Loading timeline…')).toBeInTheDocument();
  });

  test('RunStatsSkeleton announces the stats are loading', () => {
    render(<RunStatsSkeleton />);
    expect(screen.getByRole('status', { name: /Loading run stats/i })).toBeInTheDocument();
  });

  test('each skeleton renders exactly one status region', () => {
    const { unmount } = render(<WorkflowListSkeleton />);
    expect(screen.getAllByRole('status')).toHaveLength(1);
    unmount();
    render(<RunStatsSkeleton />);
    expect(screen.getAllByRole('status')).toHaveLength(1);
  });
});
