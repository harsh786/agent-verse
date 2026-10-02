/**
 * One inbox for every approval kind.
 *
 * The product /approvals page (and the sidebar / top-bar badges) used to read
 * only `/governance/approvals` (goal HITL), so workflow gate approvals
 * (`/api/v1/approvals`) and workflow publish requests never showed up there.
 * This module normalises all sources into one shape, classifies them and
 * groups several approvals of the same goal / run together.
 */
import { useQuery } from "@tanstack/react-query";
import {
  governanceApi,
  workflowEngineApi,
  type ApprovalRequest,
  type WEApprovalRequest,
  type WEWorkflow,
} from "@/lib/api/client";

export type ApprovalKind = "goal" | "step" | "subtask" | "workflow" | "publish";

export const KIND_LABELS: Record<ApprovalKind, string> = {
  goal: "Goal",
  step: "Step",
  subtask: "Sub-task",
  workflow: "Workflow gate",
  publish: "Publish",
};

export const KIND_ORDER: ApprovalKind[] = ["goal", "step", "subtask", "workflow", "publish"];

export interface UnifiedApproval {
  /** Unique across sources (request ids of different stores may collide). */
  key: string;
  /** The id the source's decide endpoint takes (request id / workflow id). */
  id: string;
  kind: ApprovalKind;
  title: string;
  risk: string;
  status: string;
  created_at?: string;
  /** Approvals with the same group key belong to one goal / run. */
  groupKey: string;
  groupLabel: string;
  goalId?: string;
  workflowId?: string;
  runId?: string;
  canDecide: boolean;
  /** Why the caller cannot decide (shown instead of the buttons' effect). */
  blockedReason?: string;
  required_approvers?: number;
  approvals_received?: number;
  /** Decision ids the source accepts for approve / reject. */
  approveAction: string;
  rejectAction: string;
}

const KNOWN_KINDS = new Set<string>(KIND_ORDER);

/**
 * Goal HITL approvals carry no explicit kind, so derive it from what raised
 * them: an org approval gate (it carries `org_id`) is a mission sub-task; the
 * executor's per-step gate reads `"<tool>: <step>"` (a single-token prefix);
 * a goal-level strategy gate reads `"<strategy> <what>: ..."` or free text.
 */
export function classifyGovernanceApproval(a: ApprovalRequest): ApprovalKind {
  if (a.kind && KNOWN_KINDS.has(a.kind)) return a.kind as ApprovalKind;
  if (a.org_id) return "subtask";
  const action = a.action ?? "";
  const colon = action.indexOf(": ");
  if (colon > 0 && !/\s/.test(action.slice(0, colon))) return "step";
  return "goal";
}

function short(id: string | undefined | null): string {
  if (!id) return "";
  return id.length > 12 ? `${id.slice(0, 12)}…` : id;
}

export function fromGovernance(a: ApprovalRequest): UnifiedApproval {
  const kind = classifyGovernanceApproval(a);
  const goalId = a.goal_id || undefined;
  return {
    key: `gov:${a.request_id}`,
    id: a.request_id,
    kind,
    title: a.action ?? a.request_id,
    risk: a.risk_level ?? "medium",
    status: a.status,
    created_at: a.created_at,
    groupKey: goalId ? `goal:${goalId}` : `gov:${a.request_id}`,
    groupLabel: goalId ? `Goal ${short(goalId)}` : "Approval",
    goalId,
    canDecide: true,
    required_approvers: a.required_approvers,
    approvals_received: a.approvals_received,
    approveAction: "approve",
    rejectAction: "reject",
  };
}

const PRIORITY_TO_RISK: Record<string, string> = {
  critical: "critical", high: "high", medium: "medium", low: "low",
};

export function fromWorkflowGate(a: WEApprovalRequest): UnifiedApproval {
  const actions = (a.actions ?? []).map((x) => x.id);
  const pick = (re: RegExp, fallback: string) => actions.find((id) => re.test(id)) ?? fallback;
  const workflow = a.workflow_name || (a.workflow_id ? `Workflow ${short(a.workflow_id)}` : "Workflow");
  const step = a.step_name || a.step_id;
  const blocked = a.can_decide === false;
  return {
    key: `wf:${a.request_id}`,
    id: a.request_id,
    kind: "workflow",
    title: `${workflow} — ${step}`,
    risk: PRIORITY_TO_RISK[a.priority] ?? "medium",
    status: a.status,
    created_at: a.created_at,
    groupKey: a.run_id ? `run:${a.run_id}` : `wf:${a.request_id}`,
    groupLabel: `${workflow} · run ${short(a.run_id)}`,
    workflowId: a.workflow_id || undefined,
    runId: a.run_id || undefined,
    canDecide: !blocked,
    blockedReason: blocked
      ? `Assigned to ${a.assigned_to ?? (a.assigned_role ? `role ${a.assigned_role}` : "someone else")}`
      : undefined,
    approveAction: pick(/^(approve|approved|accept)/i, "approve"),
    rejectAction: pick(/^(reject|rejected|deny|decline)/i, "reject"),
  };
}

export function fromPublishRequest(w: WEWorkflow): UnifiedApproval {
  return {
    key: `pub:${w.id}`,
    id: w.id,
    kind: "publish",
    title: `Publish workflow "${w.name}"`,
    risk: "medium",
    status: "pending",
    created_at: w.updated_at ?? w.created_at,
    groupKey: `pub:${w.id}`,
    groupLabel: `Workflow ${w.name}`,
    workflowId: w.id,
    canDecide: true,
    approveAction: "approve",
    rejectAction: "reject",
  };
}

export interface ApprovalGroup {
  key: string;
  label: string;
  items: UnifiedApproval[];
}

/** Group by goal / run, keeping the incoming order: a group sits where its
 * first item would, and its items stay in sort order. */
export function groupApprovals(items: UnifiedApproval[]): ApprovalGroup[] {
  const groups = new Map<string, ApprovalGroup>();
  for (const item of items) {
    const g = groups.get(item.groupKey);
    if (g) g.items.push(item);
    else groups.set(item.groupKey, { key: item.groupKey, label: item.groupLabel, items: [item] });
  }
  return Array.from(groups.values());
}

function asArray<T>(data: unknown, key = "items"): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === "object" && Array.isArray((data as Record<string, unknown>)[key])) {
    return (data as Record<string, T[]>)[key];
  }
  return [];
}

export const WORKFLOW_APPROVALS_KEY = ["workflow-approvals"] as const;
export const PUBLISH_REQUESTS_KEY = ["workflow-publish-requests"] as const;

export async function fetchWorkflowGateApprovals(): Promise<WEApprovalRequest[]> {
  return asArray<WEApprovalRequest>(await workflowEngineApi.listApprovals({ per_page: 100 }));
}

export async function fetchPublishRequests(): Promise<WEWorkflow[]> {
  return asArray<WEWorkflow>(
    await workflowEngineApi.list({ status: "pending_approval", per_page: 100 }),
  ).filter((w) => w.status === "pending_approval");
}

/** Pending approvals of every kind — for the sidebar / top-bar badges. */
export function usePendingApprovalCount(opts: { enabled?: boolean; refetchInterval?: number } = {}) {
  const enabled = opts.enabled ?? true;
  const refetchInterval = opts.refetchInterval ?? 20_000;
  const goal = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval,
    enabled,
  });
  const gates = useQuery({
    queryKey: WORKFLOW_APPROVALS_KEY,
    queryFn: fetchWorkflowGateApprovals,
    refetchInterval,
    enabled,
  });
  const publish = useQuery({
    queryKey: PUBLISH_REQUESTS_KEY,
    queryFn: fetchPublishRequests,
    refetchInterval,
    enabled,
  });
  // A non-array body must not take the app shell down (see Sidebar).
  const pending = (xs: unknown) =>
    Array.isArray(xs) ? xs.filter((a) => (a as { status?: string }).status === "pending").length : 0;
  return (
    pending(goal.data) + pending(gates.data) + (Array.isArray(publish.data) ? publish.data.length : 0)
  );
}
