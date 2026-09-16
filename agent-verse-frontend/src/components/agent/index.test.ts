/** Smoke test for the components/agent barrel — verifies every re-export resolves. */
import { describe, it, expect } from 'vitest';
import * as AgentBarrel from './index';

describe('components/agent barrel exports', () => {
  it('re-exports all agent components as functions', () => {
    expect(typeof AgentBarrel.AgentReputation).toBe('function');
    expect(typeof AgentBarrel.AutonomyControl).toBe('function');
    expect(typeof AgentBarrel.MemoryBrowser).toBe('function');
    expect(typeof AgentBarrel.DiscoveryPanel).toBe('function');
    expect(typeof AgentBarrel.DecisionLog).toBe('function');
    expect(typeof AgentBarrel.ArtifactGallery).toBe('function');
    expect(typeof AgentBarrel.CanvasViewer).toBe('function');
  });
});
