import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import * as url from 'url';

const featuresDir = path.resolve(
  url.fileURLToPath(import.meta.url),
  '../..'
);

describe('Agent Builder Frontend', () => {
  it('AgentsListPage renders agent rows as clickable', () => {
    // Smoke test: verify AgentsListPage source has click navigation
    const src = fs.readFileSync(
      path.join(featuresDir, 'AgentsListPage.tsx'),
      'utf-8'
    );
    expect(src).toContain('useNavigate');
    expect(src).toContain('navigate(`/agents/${agent.agent_id}`)');
    expect(src).toContain('cursor-pointer');
    expect(src).toContain('role="button"');
  });

  it('AgentCreatePage has manual form tab', () => {
    const src = fs.readFileSync(
      path.join(featuresDir, 'AgentCreatePage.tsx'),
      'utf-8'
    );
    expect(src).toContain("mode === 'manual'");
    expect(src).toContain('Manual Configuration');
    expect(src).toContain('handleManualCreate');
    expect(src).toContain('manualForm');
  });

  it('rollback uses path param not body', () => {
    const src = fs.readFileSync(
      path.join(featuresDir, 'AgentDetailPage.tsx'),
      'utf-8'
    );
    // Should use /rollback/${snapshotId} path param
    expect(src).toMatch(/\/rollback\/\$\{snapshotId\}/);
    // Should NOT send snapshot_id in request body
    expect(src).not.toContain('body: JSON.stringify({ snapshot_id:');
  });

  it('edit form uses PUT not PATCH', () => {
    const src = fs.readFileSync(
      path.join(featuresDir, 'AgentDetailPage.tsx'),
      'utf-8'
    );
    expect(src).not.toContain('method: "PATCH"');
    expect(src).not.toContain("method: 'PATCH'");
    expect(src).toContain('method: "PUT"');
  });

  it('uses connector_ids not connector_requirements', () => {
    const src = fs.readFileSync(
      path.join(featuresDir, 'AgentDetailPage.tsx'),
      'utf-8'
    );
    expect(src).toContain('agent.connector_ids');
    expect(src).not.toContain('agent.connector_requirements');
  });

  it('AgentDetailPage has readiness check', () => {
    const src = fs.readFileSync(
      path.join(featuresDir, 'AgentDetailPage.tsx'),
      'utf-8'
    );
    expect(src).toContain('checkReadiness');
    expect(src).toContain('/readiness');
    expect(src).toContain('Check Readiness');
  });

  it('AgentDetailPage has test agent feature', () => {
    const src = fs.readFileSync(
      path.join(featuresDir, 'AgentDetailPage.tsx'),
      'utf-8'
    );
    expect(src).toContain('handleTestAgent');
    expect(src).toContain('dry_run: true');
    expect(src).toContain('Test (Dry Run)');
  });
});
