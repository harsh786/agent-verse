/**
 * HitlLinkDecisionPage — landing page for the signed approve/reject links in
 * HITL approval emails and channel notifications (HITL-05).
 *
 * The links point at `/hitl/:requestId/(approve|reject)?sig=…&exp=…`. This route
 * did not exist, so every emailed link was dead. Opening a link never decides
 * anything (prefetchers and link scanners open links too): the signed-in
 * approver confirms, and only then is the decision POSTed to
 * `/governance/hitl/:requestId/:decision` with the link's signature (HITL-10).
 */

import { type JSX, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { ApiError, apiRequest } from '@/lib/api/client';

type Decision = 'approve' | 'reject';

interface DecisionResponse {
  request_id: string;
  status: string;
  approver?: string;
}

function messageFor(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return 'This link is invalid, expired, or not for your account.';
    if (err.status === 409) return 'This request is no longer pending — it was already decided or expired.';
    if (err.status === 401) return 'Sign in as an approver to use this link.';
    if (err.status === 503) return 'Approvals are temporarily unavailable. Nothing was decided; try again.';
  }
  return 'The decision could not be recorded. Nothing was decided; try again.';
}

export function HitlLinkDecisionPage(): JSX.Element {
  const { requestId = '', decision = '' } = useParams();
  const [params] = useSearchParams();
  const sig = params.get('sig') ?? '';
  const exp = params.get('exp') ?? '';
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle');
  const [result, setResult] = useState<DecisionResponse | null>(null);
  const [error, setError] = useState('');

  const validDecision = decision === 'approve' || decision === 'reject';
  if (!validDecision || !requestId || !sig || !exp) {
    return (
      <div className="max-w-lg mx-auto p-6" role="alert">
        <h1 className="text-lg font-semibold mb-2">Invalid approval link</h1>
        <p className="text-sm text-muted-foreground mb-4">
          This link is incomplete. Open the request from the approvals inbox instead.
        </p>
        <Link className="text-sm underline" to="/approvals">Go to approvals</Link>
      </div>
    );
  }

  const verb = (decision as Decision) === 'approve' ? 'Approve' : 'Reject';

  async function confirm(): Promise<void> {
    setState('sending');
    setError('');
    try {
      const qs = new URLSearchParams({ sig, exp }).toString();
      const res = await apiRequest<DecisionResponse>(
        'POST',
        `/governance/hitl/${encodeURIComponent(requestId)}/${decision}?${qs}`,
      );
      setResult(res);
      setState('done');
    } catch (err) {
      setError(messageFor(err));
      setState('error');
    }
  }

  return (
    <div className="max-w-lg mx-auto p-6">
      <h1 className="text-lg font-semibold mb-2">{verb} agent action</h1>
      <p className="text-sm text-muted-foreground mb-4">
        Request <span className="font-mono">{requestId}</span>
      </p>
      {state === 'done' && result ? (
        <p role="status" className="text-sm">
          Request {result.status}{result.approver ? ` by ${result.approver}` : ''}.
        </p>
      ) : (
        <>
          {state === 'error' && (
            <p role="alert" className="text-sm text-red-600 mb-3">{error}</p>
          )}
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void confirm()}
              disabled={state === 'sending'}
              className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm disabled:opacity-50"
            >
              {state === 'sending' ? 'Recording…' : `Confirm ${verb.toLowerCase()}`}
            </button>
            <Link className="px-4 py-2 text-sm underline" to="/approvals">Cancel</Link>
          </div>
        </>
      )}
    </div>
  );
}
