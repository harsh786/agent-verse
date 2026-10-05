import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { tenantsApi } from '@/lib/api/client';

/**
 * D3 — the tenant switch for the public A2A agent directory (/.well-known/agents).
 * Off by default; admin only. Agents also have to opt in individually (agent page).
 */
export function A2ADirectorySetting() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ['tenant', 'a2a-directory'],
    queryFn: () => tenantsApi.getA2ADirectory(),
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => tenantsApi.setA2ADirectory(enabled),
    onSuccess: (data) => qc.setQueryData(['tenant', 'a2a-directory'], data),
  });
  const enabled = query.data?.enabled === true;
  const error = query.error ?? toggle.error;

  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <h3 className="text-base font-semibold">Public A2A agent directory</h3>
        <p className="text-sm text-muted-foreground mt-1">
          Lists the agents you opt in (name, public description, skills and endpoint only) at
          /.well-known/agents. Off by default.
        </p>
        {error && (
          <p role="alert" className="text-sm text-red-600 dark:text-red-400 mt-1">
            {error instanceof Error ? error.message : String(error)}
          </p>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label="Public A2A agent directory"
        disabled={!query.isSuccess || toggle.isPending}
        onClick={() => toggle.mutate(!enabled)}
        className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
          enabled ? 'bg-primary' : 'bg-muted'
        }`}
      >
        <span
          className={`inline-block h-5 w-5 rounded-full bg-background shadow transform transition-transform mt-0.5 ${
            enabled ? 'translate-x-5' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}
