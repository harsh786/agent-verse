import { toast } from '@/stores/toast';
/**
 * PART 34 — OrgRealtimeManager.
 *
 * Manages the org-level SSE event stream and dispatches all 25+ org events
 * to the appropriate frontend state stores.
 *
 * All event types from spec PART 29:
 *   org.mission.* | org.team.* | org.agent.* | org.approval.*
 *   org.budget.* | org.policy.* | org.model.* | org.memory.*
 *   org.artifact.* | org.decision.* | org.health.* | org.digest.*
 */
import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { API_BASE, apiFetch } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { orgKeys } from './hooks/useOrg';

// ── Event type constants ───────────────────────────────────────────────────────

export const ORG_EVENTS = {
  MISSION_CREATED:       'org.mission.created',
  MISSION_STARTED:       'org.mission.started',
  MISSION_COMPLETED:     'org.mission.completed',
  MISSION_FAILED:        'org.mission.failed',
  MISSION_PAUSED:        'org.mission.paused',
  MISSION_BLOCKED:       'org.mission.blocked',
  TEAM_FORMING:          'org.team.forming',
  TEAM_FORMED:           'org.team.formed',
  TEAM_DISBANDED:        'org.team.disbanded',
  AGENT_ACTIVATED:       'org.agent.activated',
  AGENT_IDLE:            'org.agent.idle',
  AGENT_BLOCKED:         'org.agent.blocked',
  AGENT_ESCALATED:       'org.agent.escalated',
  AGENT_COMPLETED_TASK:  'org.agent.completed_task',
  AGENT_FAILED_TASK:     'org.agent.failed_task',
  APPROVAL_REQUESTED:    'org.approval.requested',
  APPROVAL_GRANTED:      'org.approval.granted',
  APPROVAL_REJECTED:     'org.approval.rejected',
  APPROVAL_TIMEOUT:      'org.approval.timeout',
  BUDGET_THRESHOLD_80:   'org.budget.threshold_80',
  BUDGET_THRESHOLD_95:   'org.budget.threshold_95',
  BUDGET_EXCEEDED:       'org.budget.exceeded',
  POLICY_VIOLATION:      'org.policy.violation',
  MODEL_FALLBACK:        'org.model.fallback',
  MODEL_DEGRADED:        'org.model.degraded',
  MEMORY_PROMOTED:       'org.memory.promoted',
  ARTIFACT_CREATED:      'org.artifact.created',
  ARTIFACT_APPROVED:     'org.artifact.approved',
  DECISION_RECORDED:     'org.decision.recorded',
  LEARNING_PROMOTED:     'org.learning.promoted',
  HEALTH_DEGRADED:       'org.health.degraded',
  HEALTH_RECOVERED:      'org.health.recovered',
  DIGEST_READY:          'org.digest.ready',
  EMERGENCY_STOP:        'org.emergency_stop.triggered',
  EMERGENCY_RESUMED:     'org.emergency_stop.resumed',
} as const;

export type OrgEventType = typeof ORG_EVENTS[keyof typeof ORG_EVENTS];

export interface OrgEvent {
  event_type:     OrgEventType;
  org_id:         string;
  tenant_id:      string;
  payload:        Record<string, unknown>;
  timestamp:      string;
  correlation_id?: string;
  version:        string;
}

// ── OrgRealtimeManager ────────────────────────────────────────────────────────

export class OrgRealtimeManager {
  private orgId: string;
  private apiKey: string;
  private eventSource: EventSource | null = null;
  private queryClient: ReturnType<typeof useQueryClient> | null = null;
  private onEvent?: (event: OrgEvent) => void;
  private onConnected?: () => void;
  private onDisconnected?: () => void;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 2000;

  constructor(orgId: string, apiKey = '') {
    this.orgId = orgId;
    this.apiKey = apiKey;
  }

  connect(options: {
    queryClient?: ReturnType<typeof useQueryClient>;
    onEvent?: (event: OrgEvent) => void;
    onConnected?: () => void;
    onDisconnected?: () => void;
  } = {}): void {
    this.queryClient = options.queryClient ?? null;
    this.onEvent = options.onEvent;
    this.onConnected = options.onConnected;
    this.onDisconnected = options.onDisconnected;

    void this._openEventSource();
  }

  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this._closeEventSource();
  }

  private async _openEventSource(): Promise<void> {
    if (this.eventSource) {
      this.eventSource.close();
    }

    // EventSource can't send headers and the app talks to the backend cross-origin,
    // so auth goes in the URL. Exchange the permanent API key for a short-lived,
    // read-only stream token (re-fetched on every (re)connect) so the key itself
    // never lands in a stream URL / access log. Fall back to api_key only if the
    // token mint fails, so the stream still works.
    let auth = '';
    try {
      const { token } = await apiFetch<{ token: string }>('/tenants/stream-token');
      auth = `?token=${encodeURIComponent(token)}`;
    } catch {
      if (this.apiKey) auth = `?api_key=${encodeURIComponent(this.apiKey)}`;
    }
    const url = `${API_BASE}/v1/org/${this.orgId}/events/stream${auth}`;
    this.eventSource = new EventSource(url);

    this.eventSource.onopen = () => {
      this.reconnectDelay = 2000; // reset backoff on successful connect
      this.onConnected?.();
    };

    this.eventSource.onmessage = (rawEvent) => {
      try {
        const event: OrgEvent = JSON.parse(rawEvent.data);
        this._handleEvent(event);
        this.onEvent?.(event);
      } catch {
        // Ignore malformed events
      }
    };

    this.eventSource.onerror = () => {
      this._closeEventSource();
      this.onDisconnected?.();
      // Exponential backoff reconnect
      this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, 30_000);
      this.reconnectTimer = setTimeout(() => void this._openEventSource(), this.reconnectDelay);
    };
  }

  private _closeEventSource(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  /**
   * Dispatch org events to TanStack Query cache invalidations.
   * Each event type invalidates the relevant query keys so
   * components automatically re-fetch fresh data.
   */
  private _handleEvent(event: OrgEvent): void {
    const qc = this.queryClient;
    if (!qc) return;

    const orgId = event.org_id;
    const payload = event.payload as Record<string, string>;

    switch (event.event_type) {
      // ── Mission events ──────────────────────────────────────────────────────
      case ORG_EVENTS.MISSION_CREATED:
      case ORG_EVENTS.MISSION_STARTED:
      case ORG_EVENTS.MISSION_COMPLETED:
      case ORG_EVENTS.MISSION_FAILED:
      case ORG_EVENTS.MISSION_PAUSED:
      case ORG_EVENTS.MISSION_BLOCKED:
        // 3-element prefix (no trailing filter): orgKeys.missions(orgId) yields
        // [...,'missions',undefined], which does NOT partial-match the live query
        // key [...,'missions',{}] — so invalidating with it silently refreshed
        // nothing and the mission list never updated on live events.
        qc.invalidateQueries({ queryKey: ['orgs', orgId, 'missions'] });
        qc.invalidateQueries({ queryKey: orgKeys.health(orgId) });
        if (payload.mission_id) {
          qc.invalidateQueries({
            queryKey: orgKeys.mission(orgId, payload.mission_id),
          });
        }
        // Refresh health score on mission completion/failure
        if ([ORG_EVENTS.MISSION_COMPLETED, ORG_EVENTS.MISSION_FAILED].includes(event.event_type as never)) {
          qc.invalidateQueries({ queryKey: orgKeys.health(orgId) });
        }
        break;

      // ── Team events ─────────────────────────────────────────────────────────
      case ORG_EVENTS.TEAM_FORMING:
      case ORG_EVENTS.TEAM_FORMED:
      case ORG_EVENTS.TEAM_DISBANDED:
        qc.invalidateQueries({ queryKey: ['teams', orgId] });
        break;

      // ── Agent events ────────────────────────────────────────────────────────
      case ORG_EVENTS.AGENT_ACTIVATED:
      case ORG_EVENTS.AGENT_IDLE:
      case ORG_EVENTS.AGENT_BLOCKED:
      case ORG_EVENTS.AGENT_ESCALATED:
      case ORG_EVENTS.AGENT_COMPLETED_TASK:
      case ORG_EVENTS.AGENT_FAILED_TASK:
        qc.invalidateQueries({ queryKey: ['agents', orgId] });
        qc.invalidateQueries({ queryKey: ['orgs', orgId, 'tasks'] });
        break;

      // ── Approval events ─────────────────────────────────────────────────────
      case ORG_EVENTS.APPROVAL_REQUESTED:
        qc.invalidateQueries({ queryKey: ['approvals', orgId] });
        qc.invalidateQueries({ queryKey: orgKeys.health(orgId) });
        // G-08: Show amber toast with link to approvals page
        toast({
          kind: 'warning',
          message: payload?.action ? `⏳ Approval Required: ${payload.action}` : '⏳ A mission step requires your approval.',
        });
        break;
      case ORG_EVENTS.APPROVAL_GRANTED:
        qc.invalidateQueries({ queryKey: ['approvals', orgId] });
        qc.invalidateQueries({ queryKey: orgKeys.health(orgId) });
        toast({
          kind: 'success',
          message: payload?.approver ? `✅ Approved by ${payload.approver}` : '✅ Approval granted.',
        });
        break;
      case ORG_EVENTS.APPROVAL_REJECTED:
        qc.invalidateQueries({ queryKey: ['approvals', orgId] });
        qc.invalidateQueries({ queryKey: orgKeys.health(orgId) });
        toast({
          kind: 'error',
          message: payload?.action ? `❌ Rejected: "${payload.action}"` : '❌ Approval rejected.',
        });
        break;
      case ORG_EVENTS.APPROVAL_TIMEOUT:
        qc.invalidateQueries({ queryKey: ['approvals', orgId] });
        qc.invalidateQueries({ queryKey: orgKeys.health(orgId) });
        toast({
          kind: 'error',
          message: payload?.action ? `⏰ Timed out: "${payload.action}" auto-rejected.` : '⏰ An approval timed out.',
        });
        break;

      // ── Budget events ───────────────────────────────────────────────────────
      case ORG_EVENTS.BUDGET_THRESHOLD_80:
      case ORG_EVENTS.BUDGET_THRESHOLD_95:
      case ORG_EVENTS.BUDGET_EXCEEDED:
        qc.invalidateQueries({ queryKey: orgKeys.health(orgId) });
        qc.invalidateQueries({ queryKey: ['org-analytics', orgId] });
        break;

      // ── Model events ────────────────────────────────────────────────────────
      case ORG_EVENTS.MODEL_FALLBACK:
      case ORG_EVENTS.MODEL_DEGRADED:
        qc.invalidateQueries({ queryKey: ['model-usage', orgId] });
        break;

      // ── Memory events ────────────────────────────────────────────────────────
      case ORG_EVENTS.MEMORY_PROMOTED:
        qc.invalidateQueries({ queryKey: ['org-memory', orgId] });
        break;

      // ── Artifact events ──────────────────────────────────────────────────────
      case ORG_EVENTS.ARTIFACT_CREATED:
      case ORG_EVENTS.ARTIFACT_APPROVED:
        qc.invalidateQueries({ queryKey: ['artifacts', orgId] });
        break;

      // ── Decision events ──────────────────────────────────────────────────────
      case ORG_EVENTS.DECISION_RECORDED:
        qc.invalidateQueries({ queryKey: ['decisions', orgId] });
        break;

      // ── Health events ────────────────────────────────────────────────────────
      case ORG_EVENTS.HEALTH_DEGRADED:
      case ORG_EVENTS.HEALTH_RECOVERED:
        qc.invalidateQueries({ queryKey: orgKeys.health(orgId) });
        break;

      // ── Digest ready ─────────────────────────────────────────────────────────
      case ORG_EVENTS.DIGEST_READY:
        qc.invalidateQueries({ queryKey: ['digest', orgId] });
        break;

      // ── All other events — refresh health ────────────────────────────────────
      default:
        qc.invalidateQueries({ queryKey: orgKeys.events(orgId) });
        break;
    }

    // Always refresh event feed
    qc.invalidateQueries({ queryKey: orgKeys.events(orgId) });
  }
}

// ── React hook ────────────────────────────────────────────────────────────────

/**
 * React hook that manages an OrgRealtimeManager lifecycle.
 * Connects on mount, disconnects on unmount.
 * Returns { connected, lastEvent }.
 */
export function useOrgRealtimeManager(
  orgId: string | null | undefined,
  options: {
    onEvent?: (event: OrgEvent) => void;
    onConnected?: () => void;
    onDisconnected?: () => void;
  } = {},
): { connected: boolean } {
  const qc = useQueryClient();
  const apiKey = useAuthStore(s => s.apiKey);
  const managerRef = useRef<OrgRealtimeManager | null>(null);
  const connectedRef = useRef(false);

  const { onEvent, onConnected, onDisconnected } = options;

  useEffect(() => {
    if (!orgId || !apiKey) return;

    const manager = new OrgRealtimeManager(orgId, apiKey);
    managerRef.current = manager;

    manager.connect({
      queryClient: qc,
      onEvent,
      onConnected: () => {
        connectedRef.current = true;
        onConnected?.();
      },
      onDisconnected: () => {
        connectedRef.current = false;
        onDisconnected?.();
      },
    });

    return () => {
      manager.disconnect();
      managerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId, qc, apiKey]);

  return { connected: connectedRef.current };
}
