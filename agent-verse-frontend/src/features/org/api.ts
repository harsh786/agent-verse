/**
 * API client for the AI Organization OS.
 * All calls go through the shared apiFetch/apiRequest from lib/api/client.ts.
 */
import { apiFetch } from '@/lib/api/client';
import type {
  Organization,
  OrgDepartment,
  OrgMission,
  OrgTask,
  OrgEvent,
  OrgHealthResponse,
  CursorPage,
  CreateOrganizationRequest,
  UpdateOrganizationRequest,
  CreateMissionRequest,
  CreateDepartmentRequest,
} from './types';

const BASE = '/v1/org';

export const orgApi = {
  // ── Organizations ─────────────────────────────────────────────────────────

  create(req: CreateOrganizationRequest): Promise<Organization> {
    return apiFetch<Organization>(BASE, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  },

  list(params?: { status?: string; limit?: number; cursor?: string }): Promise<CursorPage<Organization>> {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    if (params?.limit)  qs.set('limit', String(params.limit));
    if (params?.cursor) qs.set('cursor', params.cursor);
    return apiFetch<CursorPage<Organization>>(`${BASE}?${qs}`);
  },

  get(orgId: string): Promise<Organization> {
    return apiFetch<Organization>(`${BASE}/${orgId}`);
  },

  update(orgId: string, req: UpdateOrganizationRequest): Promise<Organization> {
    return apiFetch<Organization>(`${BASE}/${orgId}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  },

  delete(orgId: string): Promise<void> {
    return apiFetch<void>(`${BASE}/${orgId}`, { method: 'DELETE' });
  },

  health(orgId: string): Promise<OrgHealthResponse> {
    return apiFetch<OrgHealthResponse>(`${BASE}/${orgId}/health`);
  },

  // ── Departments ───────────────────────────────────────────────────────────

  createDepartment(orgId: string, req: CreateDepartmentRequest): Promise<OrgDepartment> {
    return apiFetch<OrgDepartment>(`${BASE}/${orgId}/departments`, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  },

  listDepartments(orgId: string): Promise<OrgDepartment[]> {
    return apiFetch<OrgDepartment[]>(`${BASE}/${orgId}/departments`);
  },

  // ── Missions ──────────────────────────────────────────────────────────────

  createMission(orgId: string, req: CreateMissionRequest): Promise<OrgMission> {
    return apiFetch<OrgMission>(`${BASE}/${orgId}/missions`, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  },

  listMissions(orgId: string, params?: { status?: string; priority?: string; limit?: number; cursor?: string }): Promise<CursorPage<OrgMission>> {
    const qs = new URLSearchParams();
    if (params?.status)   qs.set('status', params.status);
    if (params?.priority) qs.set('priority', params.priority);
    if (params?.limit)    qs.set('limit', String(params.limit));
    return apiFetch<CursorPage<OrgMission>>(`${BASE}/${orgId}/missions?${qs}`);
  },

  getMission(orgId: string, missionId: string): Promise<OrgMission> {
    return apiFetch<OrgMission>(`${BASE}/${orgId}/missions/${missionId}`);
  },

  updateMissionStatus(orgId: string, missionId: string, status: string): Promise<OrgMission> {
    return apiFetch<OrgMission>(`${BASE}/${orgId}/missions/${missionId}/status`, {
      method: 'POST',
      body: JSON.stringify({ status }),
    });
  },

  // ── Tasks ─────────────────────────────────────────────────────────────────

  listTasks(orgId: string, params?: { mission_id?: string; status?: string }): Promise<CursorPage<OrgTask>> {
    const qs = new URLSearchParams();
    if (params?.mission_id) qs.set('mission_id', params.mission_id);
    if (params?.status)     qs.set('status', params.status);
    return apiFetch<CursorPage<OrgTask>>(`${BASE}/${orgId}/tasks?${qs}`);
  },

  updateTaskStatus(orgId: string, taskId: string, status: string): Promise<OrgTask> {
    return apiFetch<OrgTask>(`${BASE}/${orgId}/tasks/${taskId}/status`, {
      method: 'POST',
      body: JSON.stringify({ status }),
    });
  },

  // ── Events ────────────────────────────────────────────────────────────────

  listEvents(orgId: string, limit = 20): Promise<OrgEvent[]> {
    return apiFetch<OrgEvent[]>(`${BASE}/${orgId}/events?limit=${limit}`);
  },
};
