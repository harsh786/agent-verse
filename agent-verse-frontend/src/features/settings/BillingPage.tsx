import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';

const API_BASE = import.meta.env.VITE_API_URL || '';

async function apiFetch(path: string, apiKey: string) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

function PlanBadge({ plan }: { plan: string }) {
  const colors: Record<string, string> = {
    free: 'bg-muted text-muted-foreground',
    starter: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    professional: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
    enterprise: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  };
  return (
    <span className={`px-3 py-1 rounded-full text-sm font-medium ${colors[plan] || colors.free}`}>
      {plan.charAt(0).toUpperCase() + plan.slice(1)}
    </span>
  );
}

function UsageBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
  const color = pct > 85 ? 'bg-destructive' : pct > 60 ? 'bg-amber-500' : 'bg-primary';
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="text-foreground">{label}</span>
        <span className="text-muted-foreground">
          {used.toLocaleString()} / {limit > 0 ? limit.toLocaleString() : '∞'}
        </span>
      </div>
      <div className="h-2 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function BillingPage() {
  const apiKey = useAuthStore((s) => s.apiKey) || '';

  const { data: subscription } = useQuery({
    queryKey: ['billing', 'subscription'],
    queryFn: () => apiFetch('/billing/subscription', apiKey),
    enabled: !!apiKey,
  });

  const { data: usage } = useQuery({
    queryKey: ['billing', 'usage'],
    queryFn: () => apiFetch('/billing/usage', apiKey),
    enabled: !!apiKey,
    refetchInterval: 30000,
  });

  const { data: plans } = useQuery({
    queryKey: ['billing', 'plans'],
    queryFn: () => apiFetch('/billing/plans', apiKey),
  });

  const currentPlan = subscription?.plan || 'free';
  const totalCost = usage?.total_cost_usd || 0;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Billing & Usage</h1>
        <p className="text-muted-foreground mt-1">Manage your plan and monitor usage</p>
      </div>

      {/* Current plan */}
      <div className="rounded-lg border bg-card p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold text-foreground">Current Plan</h2>
          <PlanBadge plan={currentPlan} />
        </div>
        <p className="text-muted-foreground text-sm mb-4">
          This month&apos;s total cost:{' '}
          <span className="font-semibold text-foreground">${totalCost.toFixed(4)}</span>
        </p>
        {currentPlan !== 'enterprise' && (
          <button className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:opacity-90">
            Upgrade Plan
          </button>
        )}
      </div>

      {/* Usage meters */}
      {usage && (
        <div className="rounded-lg border bg-card p-6">
          <h2 className="font-semibold text-foreground mb-4">Usage This Period</h2>
          <div className="space-y-4">
            <UsageBar label="Goals" used={usage.usage?.goals || 0} limit={1000} />
            <UsageBar label="LLM Tokens" used={usage.usage?.llm_tokens || 0} limit={1000000} />
            <UsageBar label="Tool Calls" used={usage.usage?.tool_calls || 0} limit={10000} />
          </div>
        </div>
      )}

      {/* Plan comparison */}
      {plans && (
        <div className="rounded-lg border bg-card p-6">
          <h2 className="font-semibold text-foreground mb-4">Available Plans</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {plans.plans?.map((plan: { id: string; name: string; price_usd_monthly: number | null; goals_per_day: number; features: string[] }) => (
              <div
                key={plan.id}
                className={`rounded-lg border p-4 ${plan.id === currentPlan ? 'border-primary ring-1 ring-primary' : 'border-border'}`}
              >
                <h3 className="font-semibold text-foreground">{plan.name}</h3>
                <p className="text-2xl font-bold text-foreground mt-1">
                  {plan.price_usd_monthly !== null ? `$${plan.price_usd_monthly}` : 'Custom'}
                  {plan.price_usd_monthly !== null && (
                    <span className="text-sm font-normal text-muted-foreground">/mo</span>
                  )}
                </p>
                <p className="text-xs text-muted-foreground mt-2">
                  {plan.goals_per_day.toLocaleString()} goals/day
                </p>
                {plan.id === currentPlan ? (
                  <span className="mt-3 block text-center text-xs text-primary font-medium">
                    Current Plan
                  </span>
                ) : (
                  <button className="mt-3 w-full text-center text-xs py-1.5 rounded border border-border text-foreground hover:bg-muted transition-colors">
                    Select
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
