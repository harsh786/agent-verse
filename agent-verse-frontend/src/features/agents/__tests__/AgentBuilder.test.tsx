import { describe, it, expect } from 'vitest';

// Use Vite's raw import feature to load source files as strings (no Node.js fs needed)
const agentsListSrc: string = (await import('../AgentsListPage.tsx?raw')).default;
const agentCreateSrc: string = (await import('../AgentCreatePage.tsx?raw')).default;
const agentDetailSrc: string = (await import('../AgentDetailPage.tsx?raw')).default;

describe('Agent Builder Frontend', () => {
  it('AgentsListPage renders agent rows as clickable', () => {
    expect(agentsListSrc).toContain('useNavigate');
    expect(agentsListSrc).toContain('/agents/${agent.agent_id}');
    expect(agentsListSrc).toContain('cursor-pointer');
    expect(agentsListSrc).toContain('role="button"');
  });

  it('AgentCreatePage has manual form tab', () => {
    expect(agentCreateSrc).toContain("mode === 'manual'");
    expect(agentCreateSrc).toContain('Manual Configuration');
    expect(agentCreateSrc).toContain('handleManualCreate');
    expect(agentCreateSrc).toContain('manualForm');
  });

  it('rollback uses path param not body', () => {
    // Should use /rollback/${snapshotId} path param
    expect(agentDetailSrc).toMatch(/\/rollback\/\$\{snapshotId\}/);
    // Should NOT send snapshot_id in request body
    expect(agentDetailSrc).not.toContain('body: JSON.stringify({ snapshot_id:');
  });

  it('edit form uses PUT not PATCH', () => {
    expect(agentDetailSrc).not.toContain('method: "PATCH"');
    expect(agentDetailSrc).not.toContain("method: 'PATCH'");
    expect(agentDetailSrc).toContain('method: "PUT"');
  });

  it('uses connector_ids not connector_requirements', () => {
    expect(agentDetailSrc).toContain('connector_ids');
    expect(agentDetailSrc).not.toContain('connector_requirements');
  });

  it('AgentDetailPage has readiness check', () => {
    expect(agentDetailSrc).toContain('checkReadiness');
    expect(agentDetailSrc).toContain('/readiness');
    expect(agentDetailSrc).toContain('Check Readiness');
  });

  it('AgentDetailPage has test agent feature', () => {
    expect(agentDetailSrc).toContain('handleTestAgent');
    expect(agentDetailSrc).toContain('dry_run: true');
    expect(agentDetailSrc).toContain('Test (Dry Run)');
  });
});
