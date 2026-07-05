import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, CreditCard, Download, Loader2, Receipt, X, XCircle } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { toast } from '@/stores/toast';
import { apiFetch, billingApi, type RazorpayPlan } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

interface PlanLimits {
  goals?: number;
  tokens?: number;
  tool_calls?: number;
}

interface UsageData {
  goals?: number;
  llm_tokens?: number;
  tool_calls?: number;
}

interface UsageResponse {
  usage?: UsageData;
}

interface SubscriptionResponse {
  plan?: string;
  limits?: PlanLimits;
  total_cost_usd?: number;
}

interface Invoice {
  id: string;
  date: string;
  amount_usd: number;
  status: 'paid' | 'pending' | 'failed';
  pdf_url?: string;
}

// ── Razorpay script loader ─────────────────────────────────────────────────────

function useRazorpay(): boolean {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if ((window as any).Razorpay) {
      setLoaded(true);
      return;
    }
    const script = document.createElement('script');
    script.src = 'https://checkout.razorpay.com/v1/checkout.js';
    script.async = true;
    script.onload = () => setLoaded(true);
    document.head.appendChild(script);
  }, []);
  return loaded;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PlanBadge({ plan }: { plan: string }) {
  const colors: Record<string, string> = {
    free: 'bg-muted text-muted-foreground',
    starter: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
    professional: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
    enterprise: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  };
  return (
    <span className={`px-3 py-1 rounded-full text-sm font-medium ${colors[plan] ?? colors['free']}`}>
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

function EmptySection({ message }: { message: string }) {
  return (
    <div className="p-6 text-center text-muted-foreground">
      <CreditCard className="h-8 w-8 mx-auto mb-2 opacity-30" />
      <p className="text-sm">{message}</p>
    </div>
  );
}

// ── Razorpay Checkout Modal ───────────────────────────────────────────────────

interface RazorpayCheckoutProps {
  plan: string;
  onSuccess: (plan: string, paymentId: string) => void;
  onClose: () => void;
}

function RazorpayCheckout({ plan, onSuccess, onClose }: RazorpayCheckoutProps) {
  const razorpayLoaded = useRazorpay();
  const qc = useQueryClient();
  const [cycle, setCycle] = useState<'monthly' | 'annual'>('monthly');
  const [step, setStep] = useState<'choose' | 'processing' | 'success' | 'error'>('choose');
  const [successMsg, setSuccessMsg] = useState('');
  const [errorMsg, setErrorMsg] = useState('');

  const { data: plans = [] } = useQuery<RazorpayPlan[]>({
    queryKey: ['billing-plans'],
    queryFn: billingApi.getPlans,
    staleTime: 5 * 60_000,
  });

  const selectedPlan: RazorpayPlan = plans.find((p) => p.plan_id === plan) ?? {
    plan_id: plan,
    name: plan.charAt(0).toUpperCase() + plan.slice(1),
    prices: { monthly_inr: 29, annual_inr: 278, monthly_paise: 2900, annual_paise: 27840 },
    limits: {},
    razorpay_key_id: 'rzp_test_placeholder',
  };

  const price = cycle === 'monthly'
    ? selectedPlan.prices.monthly_inr
    : selectedPlan.prices.annual_inr;

  const handlePay = async () => {
    setStep('processing');
    try {
      const order = await billingApi.createOrder(plan, cycle);

      if (order.is_mock) {
        // Development / demo mode — skip real payment flow
        await billingApi.verifyPayment({
          razorpay_order_id: order.order_id,
          razorpay_payment_id: `pay_mock_${Date.now()}`,
          razorpay_signature: 'mock_signature',
          plan,
          cycle,
        });
        setSuccessMsg(`Upgraded to ${order.plan} plan! (Demo mode)`);
        setStep('success');
        qc.invalidateQueries({ queryKey: ['billing'] });
        onSuccess(plan, 'pay_mock_demo');
        return;
      }

      if (!razorpayLoaded || !(window as any).Razorpay) {
        throw new Error('Razorpay checkout script not loaded. Please refresh the page.');
      }

      const rzOptions = {
        key: order.razorpay_key_id,
        amount: order.amount,
        currency: order.currency,
        name: 'AgentVerse',
        description: `${selectedPlan.name} Plan — ${cycle}`,
        order_id: order.order_id,
        prefill: {},
        theme: { color: '#6366f1' },
        modal: {
          ondismiss: () => {
            setStep('choose');
          },
        },
        handler: async (response: {
          razorpay_order_id: string;
          razorpay_payment_id: string;
          razorpay_signature: string;
        }) => {
          try {
            const verify = await billingApi.verifyPayment({
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
              plan,
              cycle,
            });
            setSuccessMsg(verify.message);
            setStep('success');
            qc.invalidateQueries({ queryKey: ['billing'] });
            onSuccess(plan, response.razorpay_payment_id);
          } catch (e) {
            setErrorMsg(String(e));
            setStep('error');
          }
        },
      };

      const rzp = new (window as any).Razorpay(rzOptions);
      rzp.open();
      // Reset to 'choose' so UI doesn't stay on "processing" while Razorpay modal is open
      setStep('choose');
    } catch (e) {
      setErrorMsg(String(e));
      setStep('error');
    }
  };

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-card border border-border rounded-xl shadow-2xl max-w-md w-full p-6">

        {step === 'choose' && (
          <>
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2 className="text-lg font-bold">Upgrade to {selectedPlan.name}</h2>
                <p className="text-sm text-muted-foreground mt-0.5">Choose your billing cycle</p>
              </div>
              <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Billing cycle toggle */}
            <div className="grid grid-cols-2 gap-3 mb-5">
              {(['monthly', 'annual'] as const).map((c) => (
                <button
                  key={c}
                  onClick={() => setCycle(c)}
                  className={`relative p-4 border-2 rounded-xl text-left transition-all ${
                    cycle === c
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:border-primary/40'
                  }`}
                >
                  <p className="text-sm font-semibold capitalize">{c}</p>
                  <p className="text-xl font-bold mt-1">
                    ₹{c === 'monthly'
                      ? selectedPlan.prices.monthly_inr
                      : selectedPlan.prices.annual_inr}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {c === 'monthly' ? 'per month' : 'per year'}
                  </p>
                  {c === 'annual' && (
                    <span className="absolute top-2 right-2 text-[10px] bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 px-1.5 py-0.5 rounded-full font-medium">
                      Save 20%
                    </span>
                  )}
                  {cycle === c && (
                    <CheckCircle className="absolute bottom-2 right-2 h-4 w-4 text-primary" />
                  )}
                </button>
              ))}
            </div>

            {/* Plan limits */}
            {Object.keys(selectedPlan.limits).length > 0 && (
              <div className="bg-muted/30 rounded-lg p-4 mb-5 space-y-2">
                {Object.entries(selectedPlan.limits).map(([key, val]) => (
                  <div key={key} className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground capitalize">
                      {key.replace(/_/g, ' ')}
                    </span>
                    <span className="font-medium">
                      {val === -1 ? 'Unlimited' : val?.toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Pay button */}
            <button
              onClick={handlePay}
              className="w-full py-3 bg-primary text-primary-foreground font-semibold rounded-xl hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-2"
            >
              <img
                src="https://razorpay.com/favicon.ico"
                alt=""
                className="w-4 h-4"
                onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
              />
              Pay ₹{price} with Razorpay
            </button>
            <p className="text-[10px] text-center text-muted-foreground mt-2">
              Secured by Razorpay · No card stored · Cancel anytime
            </p>
          </>
        )}

        {step === 'processing' && (
          <div className="py-12 text-center">
            <Loader2 className="h-12 w-12 animate-spin text-primary mx-auto mb-4" />
            <p className="text-base font-medium">Processing payment…</p>
            <p className="text-sm text-muted-foreground mt-1">Please wait</p>
          </div>
        )}

        {step === 'success' && (
          <div className="py-12 text-center">
            <div className="w-16 h-16 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center mx-auto mb-4">
              <CheckCircle className="h-8 w-8 text-green-600" />
            </div>
            <h3 className="text-lg font-bold mb-2">Payment Successful!</h3>
            <p className="text-sm text-muted-foreground mb-6">{successMsg}</p>
            <button
              onClick={onClose}
              className="px-6 py-2 bg-primary text-primary-foreground rounded-lg hover:opacity-90"
            >
              Done
            </button>
          </div>
        )}

        {step === 'error' && (
          <div className="py-8 text-center">
            <div className="w-14 h-14 bg-red-100 dark:bg-red-900/30 rounded-full flex items-center justify-center mx-auto mb-4">
              <XCircle className="h-7 w-7 text-red-600" />
            </div>
            <h3 className="text-base font-semibold mb-2 text-red-700 dark:text-red-400">
              Payment Failed
            </h3>
            <p className="text-xs text-muted-foreground mb-4 break-words">{errorMsg}</p>
            <div className="flex gap-3 justify-center">
              <button
                onClick={() => setStep('choose')}
                className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm"
              >
                Try Again
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 border border-input rounded-lg text-sm hover:bg-muted/50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function BillingPage() {
  const apiKey = useAuthStore((s) => s.apiKey) || '';
  const location = useLocation();
  const highlightPlan = (location.state as { highlightPlan?: string } | null)?.highlightPlan;

  const [upgradeModalPlan, setUpgradeModalPlan] = useState<string | null>(null);

  const {
    data: subscription,
    error: subscriptionError,
  } = useQuery<SubscriptionResponse>({
    queryKey: ['billing', 'subscription'],
    queryFn: () => apiFetch<SubscriptionResponse>('/billing/subscription'),
    enabled: !!apiKey,
  });

  const {
    data: usage,
    error: usageError,
  } = useQuery<UsageResponse>({
    queryKey: ['billing', 'usage'],
    queryFn: () => apiFetch<UsageResponse>('/billing/usage'),
    enabled: !!apiKey,
    refetchInterval: 30_000,
  });

  const {
    data: plans = [],
    error: plansError,
  } = useQuery<RazorpayPlan[]>({
    queryKey: ['billing', 'plans'],
    queryFn: billingApi.getPlans,
    enabled: !!apiKey,
    staleTime: 5 * 60_000,
  });

  const { data: invoices = [] } = useQuery<Invoice[]>({
    queryKey: ['billing-invoices'],
    queryFn: () => apiFetch<Invoice[]>('/billing/invoices').catch(() => []),
    enabled: !!apiKey,
    staleTime: 5 * 60_000,
  });

  const currentPlan = subscription?.plan ?? 'free';
  const totalCost = subscription?.total_cost_usd ?? 0;

  // Derive usage limits from the matching Razorpay plan, or fall back to defaults
  const matchingPlan = plans.find((p) => p.plan_id === currentPlan);
  const usageLimits = {
    goals:      subscription?.limits?.goals      ?? matchingPlan?.limits?.goals_per_day      ?? 1_000,
    tokens:     subscription?.limits?.tokens     ?? matchingPlan?.limits?.tokens_per_month   ?? 1_000_000,
    tool_calls: subscription?.limits?.tool_calls ?? matchingPlan?.limits?.tool_calls_per_day ?? 10_000,
  };

  const handleUpgrade = (planId: string) => {
    setUpgradeModalPlan(planId);
  };

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Billing &amp; Usage</h1>
        <p className="text-muted-foreground mt-1">Manage your plan and monitor usage</p>
      </div>

      {/* Current plan */}
      <div className="rounded-lg border bg-card p-6">
        {subscriptionError ? (
          <EmptySection message="Billing information not available" />
        ) : (
          <>
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold text-foreground">Current Plan</h2>
              <PlanBadge plan={currentPlan} />
            </div>
            <p className="text-muted-foreground text-sm mb-4">
              This month&apos;s total cost:{' '}
              <span className="font-semibold text-foreground">${totalCost.toFixed(2)}</span>
            </p>
            {currentPlan !== 'enterprise' && (
              <button
                onClick={() => handleUpgrade('starter')}
                className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-opacity"
              >
                Upgrade Plan
              </button>
            )}
          </>
        )}
      </div>

      {/* Usage meters */}
      <div className="rounded-lg border bg-card p-6">
        <h2 className="font-semibold text-foreground mb-4">Usage This Period</h2>
        {usageError ? (
          <EmptySection message="Usage data not available" />
        ) : usage ? (
          <div className="space-y-4">
            <UsageBar label="Goals"      used={usage.usage?.goals      ?? 0} limit={usageLimits.goals} />
            <UsageBar label="LLM Tokens" used={usage.usage?.llm_tokens ?? 0} limit={usageLimits.tokens} />
            <UsageBar label="Tool Calls" used={usage.usage?.tool_calls ?? 0} limit={usageLimits.tool_calls} />
          </div>
        ) : (
          <div className="space-y-4 animate-pulse">
            {[1, 2, 3].map((i) => (
              <div key={i} className="space-y-1">
                <div className="h-4 bg-muted rounded w-1/3" />
                <div className="h-2 bg-muted rounded" />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Plan comparison */}
      <div className="rounded-lg border bg-card p-6">
        <h2 className="font-semibold text-foreground mb-4">Available Plans</h2>
        {plansError ? (
          <EmptySection message="Plan information not available" />
        ) : plans.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {plans.map((plan) => (
              <div
                key={plan.plan_id}
                className={`rounded-lg border p-4 transition-all ${
                  plan.plan_id === currentPlan
                    ? 'border-primary ring-1 ring-primary'
                    : highlightPlan === plan.plan_id
                    ? 'border-amber-400 ring-2 ring-amber-400 ring-offset-1'
                    : 'border-border'
                }`}
              >
                <h3 className="font-semibold text-foreground">{plan.name}</h3>
                <p className="text-2xl font-bold text-foreground mt-1">
                  ₹{plan.prices.monthly_inr}
                  <span className="text-sm font-normal text-muted-foreground">/mo</span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  ₹{plan.prices.annual_inr}/yr
                </p>
                <div className="mt-2 space-y-0.5">
                  {Object.entries(plan.limits)
                    .slice(0, 3)
                    .map(([key, val]) => (
                      <p key={key} className="text-xs text-muted-foreground truncate">
                        • {key.replace(/_/g, ' ')}:{' '}
                        {val === -1 ? 'Unlimited' : val?.toLocaleString()}
                      </p>
                    ))}
                </div>
                {plan.plan_id === currentPlan ? (
                  <span className="mt-3 block text-center text-xs text-primary font-medium">
                    Current Plan
                  </span>
                ) : (
                  <button
                    onClick={() => handleUpgrade(plan.plan_id)}
                    className="mt-3 w-full text-center text-xs py-1.5 rounded border border-border text-foreground hover:bg-muted transition-colors"
                  >
                    Select
                  </button>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="rounded-lg border border-border p-4 animate-pulse">
                <div className="h-4 bg-muted rounded w-2/3 mb-2" />
                <div className="h-6 bg-muted rounded w-1/2 mb-3" />
                <div className="h-3 bg-muted rounded w-full mb-1" />
                <div className="h-3 bg-muted rounded w-3/4" />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Invoice history */}
      <div className="bg-card border border-border rounded-xl p-5">
        <h3 className="text-base font-semibold mb-4 flex items-center gap-2">
          <Receipt className="h-4 w-4 text-primary" />
          Invoice History
        </h3>
        {invoices.length === 0 ? (
          <p className="text-sm text-muted-foreground">No invoices yet.</p>
        ) : (
          <div className="space-y-2">
            {invoices.map((inv) => (
              <div
                key={inv.id}
                className="flex items-center justify-between py-2 border-b border-border last:border-0"
              >
                <div>
                  <p className="text-sm font-medium">
                    {new Date(inv.date).toLocaleDateString()}
                  </p>
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full ${
                      inv.status === 'paid'
                        ? 'bg-green-100 text-green-800'
                        : 'bg-red-100 text-red-800'
                    }`}
                  >
                    {inv.status}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-sm font-medium">
                    ${(inv.amount_usd ?? 0).toFixed(2)}
                  </span>
                  {inv.pdf_url && (
                    <a
                      href={inv.pdf_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-primary hover:underline flex items-center gap-1"
                    >
                      <Download className="h-3 w-3" /> PDF
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Razorpay Checkout Modal */}
      {upgradeModalPlan && (
        <RazorpayCheckout
          plan={upgradeModalPlan}
          onSuccess={(plan, paymentId) => {
            toast({
              kind: 'success',
              message: `Upgraded to ${plan}! Payment ID: ${paymentId}`,
            });
            setUpgradeModalPlan(null);
          }}
          onClose={() => setUpgradeModalPlan(null)}
        />
      )}
    </div>
  );
}
