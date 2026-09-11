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
  TeamMembersResponse,
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

  listTeamMembers(orgId: string, teamId: string): Promise<TeamMembersResponse> {
    return apiFetch<TeamMembersResponse>(`${BASE}/${orgId}/teams/${teamId}/members`);
  },

  // ── Missions ──────────────────────────────────────────────────────────────

  /** Upload a file an agent can OCR/process at run time. Returns a server path
   *  to reference from the mission objective (multipart; apiFetch handles the
   *  FormData boundary + auth). */
  uploadAttachment(
    orgId: string,
    file: File,
  ): Promise<{
    attachment_id: string;
    path: string;
    filename: string;
    content_type: string;
    size: number;
  }> {
    const fd = new FormData();
    fd.append('file', file);
    return apiFetch(`${BASE}/${orgId}/attachments`, { method: 'POST', body: fd });
  },

  /** Instant heuristic pre-flight estimate for a goal (no mission created). */
  previewMission(
    orgId: string,
    goal: string,
  ): Promise<{
    departments: string[];
    estimated_agents: number;
    estimated_duration_hours: number;
    estimated_cost_usd: number;
    estimated_risk: string;
    confidence: number;
    success_probability: number;
    potential_blockers: string[];
  }> {
    return apiFetch(`${BASE}/${orgId}/missions/preview`, {
      method: 'POST',
      body: JSON.stringify({ goal }),
    });
  },

  createMission(orgId: string, req: CreateMissionRequest): Promise<OrgMission> {
    // Use /missions/execute which triggers MetaOrchestrator team formation
    // + dispatches to AgentGraph via GoalService — not just a DB record create.
    return apiFetch<{
      mission_id: string; title: string; status: string;
      goal_id?: string; topology?: string; departments?: string[];
      agent_count?: number; autonomy_level?: number; estimated_cost_usd?: number;
      dispatched?: boolean;
    }>(`${BASE}/${orgId}/missions/execute`, {
      method: 'POST',
      body: JSON.stringify(req),
    }).then(r => ({
      // Map execute response → OrgMission shape for downstream consumers
      id:          r.mission_id,
      org_id:      orgId,
      title:       r.title,
      status:      r.status ?? 'active',
      priority:    req.priority ?? 'medium',
      objective:   req.objective ?? '',
      metadata:    {
        goal_id:                  r.goal_id,
        dispatched:               r.dispatched,
        orchestration_plan_summary: {
          topology:     r.topology,
          departments:  r.departments ?? [],
          autonomy_level: r.autonomy_level,
          estimated_cost_usd: r.estimated_cost_usd,
        },
      },
    } as unknown as OrgMission));
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
    // Backend returns CursorPage<OrgEvent> {data:[], cursor, hasMore} — unwrap
    return apiFetch<{ data: OrgEvent[] } | OrgEvent[]>(`${BASE}/${orgId}/events?limit=${limit}`)
      .then(r => Array.isArray(r) ? r : ((r as { data: OrgEvent[] }).data ?? []));
  },
};
