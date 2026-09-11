/**
 * TypeScript types for the AI Organization OS domain.
 * Mirrors backend app/org/schemas.py exactly.
 */

export interface Organization {
  id:                 string;
  tenant_id:          string;
  name:               string;
  slug:               string;
  description:        string;
  industry:           string;
  jurisdiction:       string;
  mission:            string;
  vision:             string;
  status:             OrgStatus;
  autonomy_level:     number;       // 0-5
  risk_tolerance:     RiskTolerance;
  monthly_budget_usd: number;
  created_by:         string | null;
  created_at:         string;       // ISO 8601
  updated_at:         string;
}

export type OrgStatus      = 'active' | 'paused' | 'archived';
export type RiskTolerance  = 'low' | 'medium' | 'high';
export type MissionStatus  = 'draft' | 'queued' | 'planned' | 'active' | 'paused' | 'review' | 'completed' | 'failed' | 'cancelled' | 'archived';
export type TaskStatus     = 'draft' | 'queued' | 'planned' | 'assigned' | 'running' | 'waiting' | 'blocked' | 'review' | 'approval_required' | 'completed' | 'failed' | 'cancelled' | 'expired' | 'archived';
export type Priority       = 'low' | 'medium' | 'high' | 'critical';

export interface OrgDepartment {
  id:                 string;
  tenant_id:          string;
  org_id:             string;
  name:               string;
  purpose:            string;
  capability_domains: string[];
  parent_dept_id:     string | null;
  manager_agent_id:   string | null;
  agent_count?:       number;     // optional — may not be returned by all endpoints
  status:             string;
  created_at:         string;
  updated_at:         string;
}

export interface OrgTeam {
  id:           string;
  tenant_id:    string;
  org_id:       string;
  dept_id:      string | null;
  name:         string;
  purpose:      string;
  team_type:    string;
  status:       string;
  member_agent_ids: string[];
  agent_ids?:   string[];
  created_at:   string;
  updated_at:   string;
}

export interface TeamMemberProfile {
  id: string;
  name: string;
  role?: string;
  status?: string;
  current_task?: string;
}

export interface TeamMembersResponse {
  team_id: string;
  member_ids: string[];
  members: TeamMemberProfile[];
}

export interface OrgMission {
  id:               string;
  tenant_id:        string;
  org_id:           string;
  dept_id:          string | null;
  assigned_team_id: string | null;
  title:            string;
  objective:        string;
  why:              string;
  expected_outcome: string;
  status:           MissionStatus;
  priority:         Priority;
  source:           string;
  autonomy_level:   number | null;
  budget_usd:       number | null;
  deadline:         string | null;
  tags:             string[];
  created_by:       string | null;
  outputs:          unknown[];
  evidence:         unknown[];
  started_at:       string | null;
  completed_at:     string | null;
  created_at:       string;
  updated_at:       string;
  /** Populated by /missions/execute: goal_id, topology, departments, dispatched */
  metadata?:        Record<string, unknown>;
}

export interface OrgSchedule {
  id:               string;
  org_id:           string;
  name:             string;
  title:            string;
  objective:        string;
  priority:         string;
  autonomy_level:   number | null;
  cron_expression:  string;
  timezone:         string;
  enabled:          boolean;
  next_fire_at:     string | null;
  last_fired_at:    string | null;
  last_mission_id:  string | null;
  fire_count:       number;
  created_at:       string | null;
  publish:          OrgSchedulePublish | null;
}

/** Where a scheduled mission auto-publishes its deliverable each run. */
export interface OrgSchedulePublish {
  connector_server_id: string;
  tool_name:           string;
  arguments:           Record<string, unknown>;
  /** One-time gate: false until the org approves autonomous publishing. */
  approved:            boolean;
}

export interface OrgTask {
  id:             string;
  tenant_id:      string;
  org_id:         string;
  mission_id:     string | null;
  title:          string;
  objective:      string;
  status:         TaskStatus;
  priority:       Priority;
  assigned_to:    string | null;
  created_at:     string;
  updated_at:     string;
}

export interface OrgHealthResponse {
  health:                   string;
  active_missions:          number;
  active_teams:             number;
  pending_approvals:        number;
  task_counts:              Record<string, number>;
  event_counts_24h:         Record<string, number>;
  items_needing_attention:  number;
}

export interface OrgEvent {
  id:           string;
  org_id:       string;
  event_type:   string;
  title:        string;
  description:  string;
  severity:     string;
  entity_type:  string | null;
  entity_id:    string | null;
  created_at:   string;
}

export interface CursorPage<T> {
  data:     T[];
  cursor:   string | null;
  hasMore:  boolean;
  total?:   number | null;
}

// ── Request types ──────────────────────────────────────────────────────────

export interface CreateOrganizationRequest {
  name:               string;
  description?:       string;
  industry?:          string;
  jurisdiction?:      string;
  mission?:           string;
  vision?:            string;
  autonomy_level?:    number;
  risk_tolerance?:    RiskTolerance;
  monthly_budget_usd?: number;
  blueprint_ids?:     string[];
}

export interface UpdateOrganizationRequest {
  name?:               string;
  description?:        string;
  mission?:            string;
  vision?:             string;
  autonomy_level?:     number;
  risk_tolerance?:     RiskTolerance;
  monthly_budget_usd?: number;
  status?:             OrgStatus;
}

export interface CreateMissionRequest {
  org_id:           string;
  title:            string;
  objective?:       string;
  why?:             string;
  expected_outcome?: string;
  priority?:        Priority;
  dept_id?:         string | null;
  source?:          string;
  tags?:            string[];
  budget_usd?:      number | null;
  deadline?:        string | null;
  autonomy_level?:  number | null;
}

export interface CreateDepartmentRequest {
  org_id:              string;
  name:                string;
  purpose?:            string;
  capability_domains?: string[];
  parent_dept_id?:     string | null;
  manager_agent_id?:   string | null;
}
