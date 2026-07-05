const PLAN_LIMITS = [
  { plan: 'Free', steps: 5, tokens: '50K', rpm: 30, storage: '50MB', cost: '$0.50/goal' },
  { plan: 'Starter', steps: 10, tokens: '200K', rpm: 120, storage: '500MB', cost: '$2/goal' },
  { plan: 'Professional', steps: 20, tokens: '1M', rpm: 600, storage: '10GB', cost: '$10/goal' },
  { plan: 'Enterprise', steps: 50, tokens: '5M', rpm: 6000, storage: '100GB', cost: '$50/goal' },
];

const CONNECTOR_LIMITS = [
  { connector: 'Jira', free: '10/min', starter: '30/min', pro: '100/min', enterprise: 'Unlimited' },
  { connector: 'GitHub', free: '10/min', starter: '30/min', pro: '100/min', enterprise: 'Unlimited' },
  { connector: 'Slack', free: '5/min', starter: '20/min', pro: '60/min', enterprise: 'Unlimited' },
];

function UsageBar({ label, value, max, unit = '' }: { label: string; value: number; max: number; unit?: string }) {
  const pct = Math.min((value / max) * 100, 100);
  const color = pct > 85 ? 'bg-destructive' : pct > 60 ? 'bg-amber-500' : 'bg-primary';
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-foreground">{label}</span>
        <span className="text-muted-foreground">{value.toLocaleString()}{unit} / {max.toLocaleString()}{unit}</span>
      </div>
      <div className="h-2 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function LimitsPanel() {
  return (
    <div className="space-y-6">
      {/* Plan comparison */}
      <div className="rounded-xl border bg-card p-5 overflow-x-auto">
        <h2 className="font-semibold text-foreground mb-4">Plan Limits Comparison</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b">
              {['Plan', 'Max Steps', 'Token Budget', 'API Rate', 'Storage', 'Max Cost'].map(h => (
                <th key={h} className="pb-2 pr-4 text-xs font-medium text-muted-foreground">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PLAN_LIMITS.map(row => (
              <tr key={row.plan} className="border-b last:border-0">
                <td className="py-2 pr-4 font-medium text-foreground">{row.plan}</td>
                <td className="py-2 pr-4 text-muted-foreground">{row.steps} steps</td>
                <td className="py-2 pr-4 text-muted-foreground">{row.tokens}</td>
                <td className="py-2 pr-4 text-muted-foreground">{row.rpm} req/min</td>
                <td className="py-2 pr-4 text-muted-foreground">{row.storage}</td>
                <td className="py-2 text-muted-foreground">{row.cost}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Per-connector limits */}
      <div className="rounded-xl border bg-card p-5 overflow-x-auto">
        <h2 className="font-semibold text-foreground mb-3">Per-Connector Rate Limits</h2>
        <p className="text-xs text-muted-foreground mb-4">Protects your 3rd-party API quotas from being exhausted by agent runaway.</p>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b">
              {['Connector', 'Free', 'Starter', 'Professional', 'Enterprise'].map(h => (
                <th key={h} className="pb-2 pr-4 text-xs font-medium text-muted-foreground">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CONNECTOR_LIMITS.map(row => (
              <tr key={row.connector} className="border-b last:border-0">
                <td className="py-2 pr-4 font-medium text-foreground">{row.connector}</td>
                <td className="py-2 pr-4 text-muted-foreground">{row.free}</td>
                <td className="py-2 pr-4 text-muted-foreground">{row.starter}</td>
                <td className="py-2 pr-4 text-muted-foreground">{row.pro}</td>
                <td className="py-2 text-green-600 dark:text-green-400 font-medium">{row.enterprise}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Usage bars demo */}
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold text-foreground mb-4">Current Usage</h2>
        <div className="space-y-4">
          <UsageBar label="API Requests (this minute)" value={45} max={120} />
          <UsageBar label="Token Budget (this goal)" value={180000} max={200000} />
          <UsageBar label="Steps Consumed" value={8} max={10} />
          <UsageBar label="Storage Used" value={320} max={500} unit="MB" />
        </div>
      </div>

      {/* Burst limit explanation */}
      <div className="rounded-xl border bg-amber-50 dark:bg-amber-900/20 p-4">
        <h3 className="font-semibold text-foreground mb-1">Burst Rate Limiting</h3>
        <p className="text-sm text-muted-foreground">
          In addition to per-minute limits, AgentVerse enforces a <strong>10-second burst window</strong>.
          Free plan: 10 requests in any 10s window. This prevents spike abuse while allowing short bursts for interactive use.
        </p>
      </div>
    </div>
  );
}
