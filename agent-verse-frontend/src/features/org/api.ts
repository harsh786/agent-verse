/**
 * API client for the AI Organization OS.
 * All calls go through the shared apiFetch/apiRequest from lib/api/client.ts.
 */
import { apiFetch } from '@/lib/api/client';
import type {
  Organization,
  OrgDepartment,
  OrgMission,
  OrgSchedule,
  OrgTask,
  OrgEvent,
  OrgHealthResponse,
  TeamMembersResponse,
  CursorPage,
  CreateOrganizationRequest,
  UpdateOrganizationRequest,
  CreateMissionRequest,
  CreateDepartmentRequest,
  AutonomySettings,
  AutonomyView,
  BrainDecision,
  CollaborationMessage,
  AgentAuditEntry,
  MissionTimeline,
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

  // ── Mission schedules (autonomous, cron-driven) ───────────────────────────

  listSchedules(orgId: string): Promise<OrgSchedule[]> {
    return apiFetch<OrgSchedule[]>(`${BASE}/${orgId}/schedules`);
  },

  createSchedule(orgId: string, body: {
    title: string; objective?: string; cron_expression: string; timezone?: string;
    priority?: string; autonomy_level?: number | null; name?: string;
    publish?: {
      connector_server_id: string;
      tool_name: string;
      arguments?: Record<string, unknown>;
    };
  }): Promise<OrgSchedule> {
    return apiFetch<OrgSchedule>(`${BASE}/${orgId}/schedules`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  toggleSchedule(orgId: string, scheduleId: string, enabled: boolean): Promise<OrgSchedule> {
    return apiFetch<OrgSchedule>(`${BASE}/${orgId}/schedules/${scheduleId}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    });
  },

  /** Approve (or revoke) autonomous publishing for a schedule. Approving also
   *  releases any earlier run whose deliverable is waiting at the gate. */
  approveSchedulePublishing(
    orgId: string, scheduleId: string, approved = true,
  ): Promise<OrgSchedule & { released_missions: string[] }> {
    return apiFetch<OrgSchedule & { released_missions: string[] }>(
      `${BASE}/${orgId}/schedules/${scheduleId}/approve-publishing`,
      { method: 'POST', body: JSON.stringify({ approved }) },
    );
  },

  deleteSchedule(orgId: string, scheduleId: string): Promise<void> {
    return apiFetch<void>(`${BASE}/${orgId}/schedules/${scheduleId}`, { method: 'DELETE' });
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

// ── Autonomous Org Brain ─────────────────────────────────────────────────────

export const orgAutonomyApi = {
  /** Resolved autonomy level + defaulted brain settings for the org. */
  get(orgId: string): Promise<AutonomyView> {
    return apiFetch<AutonomyView>(`${BASE}/${orgId}/autonomy`);
  },

  /** Partial update — only the provided keys change; unrelated settings are
   *  preserved (backend shallow-merges into Organization.settings.autonomy). */
  patch(
    orgId: string,
    body: { autonomy_level?: number; settings?: Partial<AutonomySettings> },
  ): Promise<AutonomyView> {
    return apiFetch<AutonomyView>(`${BASE}/${orgId}/autonomy`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  },

  /** Recent org-brain tick decisions (audit trail), newest first. */
  decisions(orgId: string, limit?: number): Promise<BrainDecision[]> {
    return apiFetch<BrainDecision[]>(`${BASE}/${orgId}/brain/decisions?limit=${limit ?? 50}`);
  },

  /** Approve a brain-proposed mission — dispatches its goal. No-op (with
   *  `already_dispatched: true`) if it was already dispatched. */
  approveProposal(
    orgId: string,
    missionId: string,
  ): Promise<{
    goal_id?: string;
    dispatched?: boolean;
    error?: string;
    already_dispatched?: boolean;
  }> {
    return apiFetch(`${BASE}/${orgId}/brain/proposals/${missionId}/approve`, {
      method: 'POST',
    });
  },

  /** Reject a brain-proposed mission — returns the cancelled mission. */
  rejectProposal(orgId: string, missionId: string): Promise<OrgMission> {
    return apiFetch<OrgMission>(`${BASE}/${orgId}/brain/proposals/${missionId}/reject`, {
      method: 'POST',
    });
  },
};

// ── Situation Room (collaboration, agent audit, mission timeline) ──────────────

export const situationApi = {
  /** Recent inter-agent collaboration messages, newest-first as returned by the
   *  backend. Backed by org_events rows with event_type="org.collaboration.message";
   *  mapped from each row's `payload` (null-safe — legacy rows may lack it). */
  collaborationHistory(orgId: string, limit = 50): Promise<CollaborationMessage[]> {
    // Backend returns CursorPage<OrgEvent> {data:[], cursor, hasMore} — unwrap
    // exactly like orgApi.listEvents.
    return apiFetch<{ data: OrgEvent[] } | OrgEvent[]>(
      `${BASE}/${orgId}/events?event_type=org.collaboration.message&limit=${limit}`,
    )
      .then(r => (Array.isArray(r) ? r : ((r as { data: OrgEvent[] }).data ?? [])))
      .then(rows =>
        rows.map((row): CollaborationMessage => {
          const payload = (row.payload ?? {}) as Record<string, unknown>;
          const str = (v: unknown): string | undefined =>
            typeof v === 'string' ? v : undefined;
          const num = (v: unknown): number | null =>
            typeof v === 'number' ? v : null;
          return {
            id:         row.id,
            from_agent: str(payload.from_agent) ?? row.entity_id ?? '',
            to:         str(payload.to) ?? '',
            kind:       str(payload.kind) ?? '',
            message:    str(payload.message) ?? row.description ?? '',
            latency_ms: num(payload.latency_ms),
            tokens:     num(payload.tokens),
            cost_usd:   num(payload.cost_usd),
            mission_id: str(payload.mission_id) ?? null,
            at:         row.created_at,
          };
        }),
      );
  },

  /** One agent's activity audit trail (messages, events, decisions, tasks),
   *  newest-first. Backend returns a bare JSON array — not a CursorPage. */
  agentAudit(orgId: string, agentId: string, limit = 50): Promise<AgentAuditEntry[]> {
    return apiFetch<AgentAuditEntry[]>(
      `${BASE}/${orgId}/agents/${encodeURIComponent(agentId)}/audit?limit=${limit}`,
    );
  },

  /** Phase-by-phase execution timeline for a mission. */
  missionTimeline(orgId: string, missionId: string): Promise<MissionTimeline> {
    return apiFetch<MissionTimeline>(`${BASE}/${orgId}/missions/${missionId}/timeline`);
  },
};
