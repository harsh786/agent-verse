const GUARDRAIL_LAYERS = [
  { id: 0, name: 'Encoding Attack Decoder', desc: 'Homoglyphs, Base64, Leetspeak, ROT13, HTML entities', status: 'active', severity: 'critical' },
  { id: 1, name: 'Prompt Injection Scanner', desc: 'Direct injection patterns, jailbreak, DAN, role-play escape', status: 'active', severity: 'critical' },
  { id: 2, name: 'Indirect Injection Scanner', desc: 'Tool output poisoning — wraps all content in <untrusted> delimiters', status: 'active', severity: 'critical' },
  { id: 3, name: 'PII & PHI Detector', desc: 'SSN, Aadhaar, card numbers, PHI — redacts from outputs', status: 'active', severity: 'high' },
  { id: 4, name: 'Data Exfiltration Guard', desc: 'Blocks credentials/secrets in write-tool args, large payloads', status: 'active', severity: 'high' },
  { id: 5, name: 'Output Anomaly Detector', desc: 'API keys in outputs, oversized responses, repetition loops', status: 'active', severity: 'high' },
  { id: 6, name: 'LLM Judge (Cloud Destruction)', desc: 'Semantic check for cloud-destruction and finance fraud patterns', status: 'active', severity: 'high' },
];

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  high: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  medium: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
};

export function GuardrailsPanel() {
  return (
    <div className="space-y-6">
      {/* Layer overview */}
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold text-foreground mb-4">Active Guardrail Layers</h2>
        <div className="space-y-2">
          {GUARDRAIL_LAYERS.map(layer => (
            <div key={layer.id} className="flex items-center justify-between p-3 rounded-lg border bg-muted/20">
              <div className="flex items-center gap-3">
                <div className="w-6 h-6 rounded-full bg-primary/10 text-primary text-xs flex items-center justify-center font-bold">
                  {layer.id}
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">{layer.name}</p>
                  <p className="text-xs text-muted-foreground">{layer.desc}</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className={`text-xs px-2 py-0.5 rounded-full ${SEVERITY_COLORS[layer.severity]}`}>
                  {layer.severity}
                </span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
                  {layer.status}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Red-team corpus stats */}
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold text-foreground mb-3">Red-Team Test Coverage</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { category: 'Direct Injection', count: 10, color: 'text-red-600 dark:text-red-400' },
            { category: 'Indirect Injection', count: 5, color: 'text-orange-600 dark:text-orange-400' },
            { category: 'Jailbreak', count: 9, color: 'text-amber-600 dark:text-amber-400' },
            { category: 'Exfiltration', count: 9, color: 'text-yellow-600 dark:text-yellow-400' },
            { category: 'Social Engineering', count: 5, color: 'text-purple-600 dark:text-purple-400' },
            { category: 'Auth Bypass', count: 2, color: 'text-blue-600 dark:text-blue-400' },
            { category: 'Multi-Turn', count: 1, color: 'text-indigo-600 dark:text-indigo-400' },
            { category: 'Legitimate (Pass)', count: 8, color: 'text-green-600 dark:text-green-400' },
          ].map(({ category, count, color }) => (
            <div key={category} className="rounded-lg border bg-muted/20 p-3 text-center">
              <div className={`text-2xl font-bold ${color}`}>{count}</div>
              <div className="text-xs text-muted-foreground mt-0.5">{category}</div>
            </div>
          ))}
        </div>
        <div className="mt-3 p-3 rounded-lg bg-green-50 dark:bg-green-900/20 text-xs text-green-700 dark:text-green-400">
          51 total red-team cases · All blocking cases verified to trigger guardrails in CI
        </div>
      </div>
    </div>
  );
}
