import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch, ApiError } from '@/lib/api/client';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { MessageSquare, Plus, CheckCircle, AlertCircle, Clock, ShieldCheck, Ban, History, UserCheck } from 'lucide-react';

/**
 * TRG-03: a channel is claimed `pending_verification` and routes nothing until a
 * message on the channel itself carries the one-time code. Mappings created
 * before ownership proof existed are `legacy_unverified`: they keep routing
 * until verified.
 *
 * On channels anyone can send to (SMS number, email address, form, meeting,
 * voice) a code only proves someone can SEND there, not own it: those claims are
 * `pending_operator_approval` until a platform operator approves (`verified`) or
 * rejects (`rejected`) them, and legacy ones are under operator review. A legacy
 * mapping displaced by another organization's proof is kept as `superseded`.
 */
type MappingStatus =
  | 'pending_verification'
  | 'verified'
  | 'legacy_unverified'
  | 'pending_operator_approval'
  | 'superseded'
  | 'rejected';

/** Mirrors backend OPERATOR_APPROVAL_CHANNELS (app/api/channels/verification.py). */
const OPERATOR_APPROVAL_CHANNELS = new Set(['sms', 'email', 'form', 'meeting', 'voice']);

function needsOperatorApproval(channelType: string): boolean {
  return OPERATOR_APPROVAL_CHANNELS.has(channelType.toLowerCase());
}

/** Only a code-verifiable, not-yet-proven mapping offers "Verify". */
function canRequestCode(m: ChannelMapping): boolean {
  if (m.needs_remapping || needsOperatorApproval(m.channel_type)) return false;
  return m.status === 'pending_verification' || m.status === 'legacy_unverified';
}

interface ChannelMapping {
  id: string;
  channel_type: string;
  channel_id: string;
  status?: MappingStatus;
  verified_at?: string | null;
  verification_expires_at?: string | null;
  created_at?: string;
  /** Legacy Teams mapping keyed on the shared serviceUrl — it no longer routes. */
  needs_remapping?: boolean;
}

interface IssuedMapping {
  id: string;
  channel_type: string;
  channel_id: string;
  status: MappingStatus;
  verification_code: string | null;
  verification_expires_at: string | null;
  instructions?: string;
}

function mutationErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 409) {
    return 'This channel is already claimed by another organization, which has verified it.';
  }
  return error instanceof Error ? error.message : fallback;
}

function StatusBadge({ mapping }: { mapping: ChannelMapping }) {
  if (mapping.needs_remapping) {
    return (
      <span
        className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
        title="Teams is now routed by Microsoft 365 tenant ID. Add a Teams channel with your tenant ID."
      >
        <AlertCircle className="h-3.5 w-3.5" />
        Needs re-mapping
      </span>
    );
  }
  if (mapping.status === 'verified') {
    return (
      <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
        <CheckCircle className="h-3.5 w-3.5" />
        Verified
      </span>
    );
  }
  if (mapping.status === 'pending_operator_approval') {
    return (
      <span
        className="flex items-center gap-1 text-xs text-muted-foreground"
        title="A code only proves someone can send to this channel, so a platform operator reviews the claim. Inbound events are not routed until it is approved."
      >
        <UserCheck className="h-3.5 w-3.5" />
        Awaiting operator approval
      </span>
    );
  }
  if (mapping.status === 'superseded') {
    return (
      <span
        className="flex items-center gap-1 text-xs text-muted-foreground"
        title="Another organization proved ownership of this channel. Kept for the record; it no longer routes."
      >
        <History className="h-3.5 w-3.5" />
        Superseded
      </span>
    );
  }
  if (mapping.status === 'rejected') {
    return (
      <span
        className="flex items-center gap-1 text-xs text-destructive"
        title="A platform operator rejected this claim. Inbound events are not routed."
      >
        <Ban className="h-3.5 w-3.5" />
        Rejected
      </span>
    );
  }
  if (mapping.status === 'legacy_unverified' && needsOperatorApproval(mapping.channel_type)) {
    return (
      <span
        className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
        title="Connected before ownership checks existed. It keeps routing while a platform operator reviews it."
      >
        <AlertCircle className="h-3.5 w-3.5" />
        Under operator review
      </span>
    );
  }
  if (mapping.status === 'pending_verification') {
    return (
      <span
        className="flex items-center gap-1 text-xs text-muted-foreground"
        title="Inbound events are not routed until the verification code is sent on this channel."
      >
        <Clock className="h-3.5 w-3.5" />
        Pending verification
      </span>
    );
  }
  return (
    <span
      className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
      title="Connected before ownership verification existed. It keeps routing, but verify it to secure the channel."
    >
      <AlertCircle className="h-3.5 w-3.5" />
      Unverified
    </span>
  );
}

// Teams routes inbound activities by the organisation's Microsoft 365 tenant ID.
const GUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function channelIdLabel(channelType: string): string {
  if (channelType === 'slack') return 'Workspace ID';
  if (channelType === 'email') return 'Email Address';
  if (channelType === 'teams') return 'Microsoft 365 tenant ID';
  if (channelType === 'sms') return 'Phone Number';
  return 'Channel ID';
}

function channelIdPlaceholder(channelType: string): string {
  if (channelType === 'slack') return 'T12345ABCD';
  if (channelType === 'email') return 'support@company.com';
  if (channelType === 'teams') return '00000000-0000-0000-0000-000000000000';
  if (channelType === 'sms') return '+15551234567';
  return 'channel-id';
}

const CHANNEL_ICONS: Record<string, string> = {
  slack: '💬',
  teams: '💙',
  discord: '🎮',
  email: '📧',
  sms: '📱',
  voice: '🎙️',
  form: '📝',
};

export function ChannelMappingsPage() {
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [channelType, setChannelType] = useState('slack');
  const [channelId, setChannelId] = useState('');
  const [issued, setIssued] = useState<IssuedMapping | null>(null);
  const [awaitingOperator, setAwaitingOperator] = useState<IssuedMapping | null>(null);

  const { data: mappings = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['channel-mappings'],
    queryFn: () => apiFetch<ChannelMapping[]>('/channels/mappings'),
  });

  const trimmedId = channelId.trim();
  const teamsIdInvalid = channelType === 'teams' && trimmedId !== '' && !GUID_RE.test(trimmedId);

  const createMapping = useMutation({
    mutationFn: (body: { channel_type: string; channel_id: string }) =>
      apiFetch<IssuedMapping>('/channels/mappings', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['channel-mappings'] });
      setShowAdd(false);
      setChannelId('');
      setIssued(result?.verification_code ? result : null);
      setAwaitingOperator(result?.status === 'pending_operator_approval' ? result : null);
    },
  });

  const requestCode = useMutation({
    mutationFn: (mappingId: string) =>
      apiFetch<IssuedMapping>(`/channels/mappings/${encodeURIComponent(mappingId)}/verify`, {
        method: 'POST',
      }),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['channel-mappings'] });
      setIssued(result?.verification_code ? result : null);
    },
  });

  return (
    <JARVISPageShell>
    <div className="flex flex-col gap-6 p-6 max-w-screen-lg mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <MessageSquare className="h-6 w-6 text-green-500" />
            Channel Connections
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Connect Slack, Teams, Discord, and other channels to enable conversational triggers.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Add Channel
        </button>
      </div>

      {/* One-time verification code */}
      {issued && (
        <section
          aria-label="Verify channel ownership"
          className="rounded-xl border border-primary/40 bg-primary/5 p-4 text-sm"
        >
          <div className="flex items-center gap-2 font-medium">
            <ShieldCheck className="h-4 w-4 text-primary" />
            Verify {issued.channel_type} <span className="font-mono">{issued.channel_id}</span>
          </div>
          <p className="mt-2 text-muted-foreground">
            Send this code as a message on the channel itself (for example, post it where the
            AgentVerse app is installed):
          </p>
          <p className="mt-2 font-mono text-lg tracking-wider select-all">{issued.verification_code}</p>
          <p className="mt-2 text-xs text-muted-foreground">
            {issued.verification_expires_at && <>Expires {new Date(issued.verification_expires_at).toLocaleString()}. </>}
            {issued.status === 'legacy_unverified'
              ? 'The existing connection keeps routing until it is verified.'
              : 'Inbound events are not routed until the channel is verified.'}
          </p>
          <button onClick={() => setIssued(null)} className="mt-3 text-xs underline">
            Done
          </button>
        </section>
      )}

      {/* Send-only channel: a platform operator decides */}
      {awaitingOperator && (
        <section
          aria-label="Awaiting operator approval"
          className="rounded-xl border border-border bg-muted/40 p-4 text-sm"
        >
          <div className="flex items-center gap-2 font-medium">
            <UserCheck className="h-4 w-4 text-primary" />
            {awaitingOperator.channel_type} <span className="font-mono">{awaitingOperator.channel_id}</span> is awaiting operator approval
          </div>
          <p className="mt-2 text-muted-foreground">
            A code sent to a phone number or email address only proves someone can send to it, not
            that they own it, so a platform operator reviews this claim.
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            Inbound events are not routed until it is approved.
          </p>
          <button onClick={() => setAwaitingOperator(null)} className="mt-3 text-xs underline">
            Done
          </button>
        </section>
      )}

      {requestCode.isError && (
        <p className="text-xs text-destructive" role="status">
          {mutationErrorMessage(requestCode.error, 'Could not issue a verification code.')}
        </p>
      )}

      {/* Error */}
      {isError && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive" role="alert">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Failed to load channel mappings.
          <button onClick={() => refetch()} className="ml-auto underline">Retry</button>
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="space-y-3">
          {[1, 2].map((i) => <div key={i} className="h-16 rounded-xl bg-muted animate-pulse" />)}
        </div>
      )}

      {/* Empty */}
      {!isLoading && !mappings.length && (
        <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
          <MessageSquare className="h-12 w-12 mb-4 opacity-20" />
          <p className="text-lg font-medium">No channels connected</p>
          <p className="text-sm mt-1">Connect a channel to enable conversational triggers.</p>
        </div>
      )}

      {/* Channel list */}
      {mappings.length > 0 && (
        <div className="rounded-xl border border-border bg-card overflow-hidden divide-y divide-border">
          {mappings.map((m) => (
            <div key={m.id} data-testid="channel-row" className="flex items-center gap-4 px-4 py-3">
              <span className="text-2xl" aria-label={m.channel_type}>
                {CHANNEL_ICONS[m.channel_type] ?? '🔗'}
              </span>
              <div className="flex-1">
                <div className="text-sm font-medium capitalize">{m.channel_type}</div>
                <div className="text-xs text-muted-foreground font-mono">{m.channel_id}</div>
              </div>
              <StatusBadge mapping={m} />
              {canRequestCode(m) && (
                <button
                  onClick={() => requestCode.mutate(m.id)}
                  disabled={requestCode.isPending}
                  className="rounded-lg border border-border px-3 py-1 text-xs hover:bg-muted transition-colors disabled:opacity-50"
                >
                  Verify
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Add channel modal */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Add channel connection">
          <div className="absolute inset-0 bg-black/40" onClick={() => setShowAdd(false)} />
          <div className="relative w-full max-w-md rounded-xl bg-background shadow-xl p-6">
            <h2 className="text-lg font-semibold mb-4">Add Channel</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-1.5">Channel Type</label>
                <select
                  value={channelType}
                  onChange={(e) => setChannelType(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  {Object.keys(CHANNEL_ICONS).map((ct) => (
                    <option key={ct} value={ct}>{CHANNEL_ICONS[ct]} {ct.charAt(0).toUpperCase() + ct.slice(1)}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1.5">
                  {channelIdLabel(channelType)}
                </label>
                <input
                  type="text"
                  value={channelId}
                  onChange={(e) => setChannelId(e.target.value)}
                  placeholder={channelIdPlaceholder(channelType)}
                  aria-invalid={teamsIdInvalid || undefined}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
                />
                {teamsIdInvalid && (
                  <p className="mt-1 text-xs text-destructive">
                    The Microsoft 365 tenant ID must be a GUID (Entra admin center → Overview → Tenant ID).
                  </p>
                )}
              </div>
              {createMapping.isError && (
                <p className="text-xs text-destructive" role="status">
                  {mutationErrorMessage(createMapping.error, 'Could not connect the channel.')}
                </p>
              )}
            </div>
            <div className="flex gap-2 justify-end mt-6">
              <button
                onClick={() => setShowAdd(false)}
                className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => createMapping.mutate({ channel_type: channelType, channel_id: trimmedId })}
                disabled={!trimmedId || teamsIdInvalid || createMapping.isPending}
                className="rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {createMapping.isPending ? 'Connecting…' : 'Connect Channel'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </JARVISPageShell>
  );
}
