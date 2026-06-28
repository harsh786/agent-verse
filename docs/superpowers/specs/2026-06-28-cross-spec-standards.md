# Cross-Specification Engineering Standards

All 5 world-class specs must comply with these standards universally.

## 1. Authentication on All Endpoints

Every new backend endpoint MUST:
- Call `_require_tenant(request)` as the first operation
- Return `HTTP 401` if no API key: `raise HTTPException(status_code=401, detail="Unauthorized")`
- Return `HTTP 403` if tenant doesn't own the resource
- Return `HTTP 404` if resource not found with message: `f"Resource {id} not found"`

## 2. Frontend API Calls — Never Use Raw fetch()

All frontend components MUST use the typed API client:
- Import `{ request as apiFetch }` or typed API namespaces from `@/lib/api/client`
- Never use `fetch()` directly — it bypasses SSO Bearer token, API key, and error handling
- For endpoints not yet in `client.ts`, add them before using

## 3. Migration Revision Chain

Implementation order for all 5 specs (strict sequential due to migration chain):
1. Agent Civilization (migration 0049)
2. Multi-Agent Spawning (migration 0050)
3. Loop Engineering (migration 0051)
4. Playground / Agent Lab (migration 0052)
5. RPA (no new migration — uses Redis for state)

If parallel implementation is needed, use separate Alembic branch labels and merge.

## 4. prefers-reduced-motion

Add to `src/index.css` or `src/app/globals.css`:
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```
This single rule covers all animations in all 5 specs automatically.

## 5. Mobile Responsiveness Standards

Every new page MUST:
- Use `max-w-5xl mx-auto` or similar container max-width
- Use responsive grid: `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`
- Use `p-4 md:p-6` padding
- Avoid fixed-width elements that overflow on mobile
- Charts: `<ResponsiveContainer width="100%" height={h}>` (all ThemedChart wrappers already do this)
- Tables: wrap in `overflow-x-auto` div

## 6. Lazy Loading Standards

All new pages MUST be lazy-loaded in App.tsx:
```typescript
const NewPage = lazy(() => import("@/features/.../NewPage").then(m => ({ default: m.NewPage })));
// With Suspense fallback:
<Suspense fallback={<div className="flex items-center justify-center h-64"><Loader2 className="animate-spin h-5 w-5 text-muted-foreground" /></div>}>
  <NewPage />
</Suspense>
```

## 7. Empty States

Every list/data view MUST have an empty state:
```typescript
// When data array is empty:
{items.length === 0 && !isLoading && (
  <EmptyState
    icon={RelevantIcon}
    title="No [items] yet"
    description="[Contextual instruction on how to create the first item]"
    action={<button onClick={...} className="...">Create First [Item]</button>}
  />
)}
```

## 8. Error States

Every useQuery MUST handle `isError`:
```typescript
const { data, isLoading, isError, error } = useQuery({...});
if (isError) return (
  <div role="alert" className="flex flex-col items-center justify-center h-32 text-muted-foreground">
    <AlertCircle className="h-8 w-8 opacity-40 mb-2" />
    <p className="text-sm">Failed to load data</p>
    <button onClick={() => refetch()} className="mt-2 text-xs text-primary hover:underline">Retry</button>
  </div>
);
```

## 9. Confirmation Dialogs

Destructive actions (delete, kill, abort, purge) MUST use `<ConfirmModal>`:
```typescript
const [confirmOpen, setConfirmOpen] = useState(false);
<ConfirmModal
  open={confirmOpen}
  title="Delete [item]?"
  description="This cannot be undone."
  confirmLabel="Delete"
  variant="danger"
  isLoading={deleteMutation.isPending}
  onConfirm={() => deleteMutation.mutate(id)}
  onCancel={() => setConfirmOpen(false)}
/>
```

## 10. Toast Notifications

Every mutation MUST have success + error toasts:
```typescript
const mutation = useMutation({
  mutationFn: ...,
  onSuccess: () => toast({ kind: "success", message: "Action completed." }),
  onError: (e) => toast({ kind: "error", message: `Failed: ${String(e)}` }),
});
```

## 11. Sidebar Registration

Every new top-level page MUST be added to `src/components/ui/Sidebar.tsx`:
- Add the appropriate import from lucide-react
- Add to the appropriate NAV_SECTION
- If the feature is gated (e.g., Civilization requires CIVILIZATION_ENABLED), add a conditional

## 12. App.tsx Route Registration

Every new page MUST have:
1. Lazy import
2. Route in RequireAuth block
3. Suspense wrapper with spinner fallback
4. Sidebar link (see above)

## 13. Rate Limiting on Expensive Endpoints

Endpoints that could be expensive (time-series queries, full-table scans, LLM calls) MUST have:
- Query parameter limits: `hours: int = Query(default=24, ge=1, le=168)`, `limit: int = Query(default=50, ge=1, le=200)`
- Response size limits: truncate arrays at 1000 items
- Caching headers: `Cache-Control: max-age=30` for read-heavy endpoints

## 14. Dark Mode Compliance

All new UI components MUST use only CSS variables:
- Use: `bg-card`, `bg-background`, `bg-muted`, `text-foreground`, `text-muted-foreground`, `border-border`
- Use: `dark:bg-*/dark:text-*` variants for colored elements
- Never use: `bg-white`, `bg-gray-*`, `text-gray-*`, `border-gray-*` (hardcoded colors)

## 15. TypeScript Strictness

All new files MUST:
- Have explicit return types on exported functions
- Have no `any` casts without a `// eslint-disable-line @typescript-eslint/no-explicit-any` comment explaining why
- Export all interfaces used across more than one file from `client.ts`
- Pass `npx tsc --noEmit` without errors
