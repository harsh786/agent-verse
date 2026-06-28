import { useToastStore } from '@/stores/toast';

const KIND_CLASS: Record<string, string> = {
  success: 'border-green-500/40 text-green-700 dark:text-green-400',
  error: 'border-red-500/40 text-destructive',
  info: 'border-border text-foreground',
};

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2" role="region" aria-label="Notifications">
      {toasts.map((t) => (
        <div key={t.id} role="status"
          className={`flex items-start gap-3 bg-card border rounded-lg shadow-lg px-4 py-3 text-sm max-w-sm ${KIND_CLASS[t.kind]}`}>
          <span className="flex-1">{t.message}</span>
          <button aria-label="Dismiss" onClick={() => dismiss(t.id)} className="text-muted-foreground hover:text-foreground">×</button>
        </div>
      ))}
    </div>
  );
}
