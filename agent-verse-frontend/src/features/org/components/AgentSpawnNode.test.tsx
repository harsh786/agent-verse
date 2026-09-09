import { render } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { AgentSpawnNode } from './AgentSpawnNode';

describe('AgentSpawnNode', () => {
  it('fires onSpawn exactly once, on mount — the real spawn signal', () => {
    const onSpawn = vi.fn();
    const pos = { x: 10, y: 20 };
    const { rerender } = render(
      <AgentSpawnNode pos={pos} onSpawn={onSpawn}>
        <span>agent</span>
      </AgentSpawnNode>
    );
    expect(onSpawn).toHaveBeenCalledTimes(1);
    expect(onSpawn).toHaveBeenCalledWith(pos);

    // Re-rendering the SAME mounted node (e.g. position ticks from d3) must
    // not re-fire the burst — only a fresh mount (a real new agent) does.
    rerender(
      <AgentSpawnNode pos={{ x: 11, y: 21 }} onSpawn={onSpawn}>
        <span>agent</span>
      </AgentSpawnNode>
    );
    expect(onSpawn).toHaveBeenCalledTimes(1);
  });

  it('does not call onSpawn under prefers-reduced-motion (no fabricated burst)', () => {
    const onSpawn = vi.fn();
    render(
      <AgentSpawnNode pos={{ x: 0, y: 0 }} onSpawn={onSpawn} reduce>
        <span>agent</span>
      </AgentSpawnNode>
    );
    expect(onSpawn).not.toHaveBeenCalled();
  });

  it('renders its children', () => {
    const { getByText } = render(
      <AgentSpawnNode pos={{ x: 0, y: 0 }}>
        <span>Agent Smith</span>
      </AgentSpawnNode>
    );
    expect(getByText('Agent Smith')).toBeInTheDocument();
  });
});
