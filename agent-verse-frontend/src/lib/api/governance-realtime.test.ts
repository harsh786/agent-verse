import { afterEach, expect, test, vi } from 'vitest';
import { notificationsApi, auditApi, rbacApi, complianceApi } from '@/lib/api/client';

afterEach(() => vi.restoreAllMocks());

function mockOk(body: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' },
    }),
  );
}

test('auditApi.query builds the governance/audit query string', async () => {
  const f = mockOk([]);
  await auditApi.query({ tool_name: 'jira.delete', limit: 50, start_time: '2026-01-01' });
  const url = String(f.mock.calls[0][0]);
  expect(url).toContain('/governance/audit');
  expect(url).toContain('tool_name=jira.delete');
  expect(url).toContain('limit=50');
  expect(url).toContain('start_time=2026-01-01');
});

test('notificationsApi.create posts channel_type+config', async () => {
  const f = mockOk({ channel_id: 'c1', type: 'webhook', status: 'created' }, 201);
  await notificationsApi.create({ channel_type: 'webhook', config: { url: 'https://x' } });
  const [url, init] = f.mock.calls[0];
  expect(String(url)).toContain('/governance/notifications');
  expect(JSON.parse(String((init as RequestInit).body))).toEqual({
    channel_type: 'webhook', config: { url: 'https://x' },
  });
});

test('rbacApi.createRole posts user_id+role', async () => {
  const f = mockOk({ id: 'r1', user_id: 'u1', role: 'approver', tenant_id: 't' }, 201);
  await rbacApi.createRole('u1', 'approver');
  const init = f.mock.calls[0][1] as RequestInit;
  expect(JSON.parse(String(init.body))).toEqual({ user_id: 'u1', role: 'approver' });
});

test('rbacApi.addIpAllowlist posts cidr+description', async () => {
  const f = mockOk({ id: 'e1', cidr: '10.0.0.0/8', description: 'office' }, 201);
  await rbacApi.addIpAllowlist('10.0.0.0/8', 'office');
  const url = String(f.mock.calls[0][0]);
  expect(url).toContain('/tenants/me/ip-allowlist');
});

test('complianceApi.getGdprExportStatus hits compliance/export/jobs/{id}', async () => {
  const f = mockOk({ job_id: 'j1', status: 'pending', completed_at: null, download_url: null, error: null });
  await complianceApi.getGdprExportStatus('j1');
  expect(String(f.mock.calls[0][0])).toContain('/compliance/export/jobs/j1');
});
