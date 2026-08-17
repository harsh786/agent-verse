---
applyTo: "agent-verse-frontend/src/**/*.{ts,tsx}"
---

# Frontend Coding Instructions — AgentVerse

## Feature Slice Structure

Every feature lives in `src/features/<domain>/`. Structure:

```
src/features/<domain>/
  index.ts                    # Public exports only
  <Domain>Page.tsx            # Route-level page component (lazy loaded)
  components/
    <Domain>List.tsx          # List view (virtualized if >50 items)
    <Domain>Card.tsx          # Card component (memoized)
    <Domain>Form.tsx          # Form component (controlled)
    <Domain>Detail.tsx        # Detail/expanded view
  hooks/
    use<Domain>.ts            # TanStack Query hooks
    use<Domain>Store.ts       # Zustand slice (if needed)
  types.ts                    # Domain types
  api.ts                      # API call functions (no direct fetch in components)
```

## Route-Level Code Splitting (Always)

```tsx
// src/app/routes.tsx
import { lazy, Suspense } from 'react';
import { PageSkeleton } from '@/components/ui/PageSkeleton';
import { ErrorBoundary } from '@/components/ErrorBoundary';

const MissionsPage = lazy(() => import('@/features/goals/MissionsPage'));
const KnowledgePage = lazy(() => import('@/features/knowledge/KnowledgePage'));

// Every route:
<ErrorBoundary name="MissionsRoute">
  <Suspense fallback={<PageSkeleton />}>
    <MissionsPage />
  </Suspense>
</ErrorBoundary>
```

## TanStack Query — Server State (Always)

```typescript
// src/features/goals/hooks/useMissions.ts
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import type { Mission, CreateMissionRequest } from '../types';

// Keys factory — centralizes all cache keys
export const missionKeys = {
  all:    (orgId: string) => ['missions', orgId] as const,
  detail: (id: string)   => ['mission', id] as const,
};

export function useMissions(orgId: string) {
  return useQuery({
    queryKey:  missionKeys.all(orgId),
    queryFn:   () => api.missions.list(orgId),
    staleTime: 30_000,      // 30s — don't refetch if fresh
    gcTime:    5 * 60_000,  // 5min in cache after unmount
  });
}

export function useCreateMission(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateMissionRequest) => api.missions.create(orgId, req),
    // Optimistic update — instant UI feedback
    onMutate: async (req) => {
      await qc.cancelQueries({ queryKey: missionKeys.all(orgId) });
      const prev = qc.getQueryData(missionKeys.all(orgId));
      qc.setQueryData(missionKeys.all(orgId), (old: Mission[] = []) => [
        { id: 'temp', ...req, status: 'pending', createdAt: new Date().toISOString() },
        ...old,
      ]);
      return { prev };
    },
    onError: (_, __, context) => {
      qc.setQueryData(missionKeys.all(orgId), context?.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: missionKeys.all(orgId) });
    },
  });
}
```

## Zustand — Client State (Only for UI State)

```typescript
// src/stores/orgStore.ts
import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';

interface OrgStore {
  selectedOrgId: string | null;
  commandBarOpen: boolean;
  sidebarCollapsed: boolean;
  // Actions:
  setSelectedOrg:   (id: string) => void;
  toggleCommandBar: () => void;
  toggleSidebar:    () => void;
}

export const useOrgStore = create<OrgStore>()(
  devtools(
    persist(
      (set) => ({
        selectedOrgId:    null,
        commandBarOpen:   false,
        sidebarCollapsed: false,
        setSelectedOrg:   (id) => set({ selectedOrgId: id }),
        toggleCommandBar: () => set((s) => ({ commandBarOpen: !s.commandBarOpen })),
        toggleSidebar:    () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      }),
      {
        name:        'org-ui-prefs',
        partialize:  (s) => ({ sidebarCollapsed: s.sidebarCollapsed }),  // persist only prefs
      }
    )
  )
);
```

## React Memoization (Performance-Critical Components)

```tsx
// Memoize expensive components:
const MissionCard = React.memo(function MissionCard(
  { mission, onSelect }: { mission: Mission; onSelect: (id: string) => void }
) {
  return <div>{mission.title}</div>;
}, (prev, next) =>
  prev.mission.id === next.mission.id &&
  prev.mission.status === next.mission.status
);

// Memoize expensive computations:
const sortedMissions = useMemo(
  () => missions.slice().sort((a, b) => b.priority - a.priority),
  [missions]
);

// Stable callback references:
const handleSelect = useCallback((id: string) => {
  setSelectedId(id);
  onSelect?.(id);
}, [onSelect]);
```

## Virtualized Lists (Required for >50 Items)

```tsx
import { useVirtualizer } from '@tanstack/react-virtual';

function MissionsList({ missions }: { missions: Mission[] }) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count:            missions.length,
    getScrollElement: () => parentRef.current,
    estimateSize:     () => 72,     // row height in px
    overscan:         5,
  });

  return (
    <div ref={parentRef} className="h-[600px] overflow-auto">
      <div style={{ height: virtualizer.getTotalSize() }} className="relative">
        {virtualizer.getVirtualItems().map((item) => (
          <MissionCard
            key={missions[item.index].id}
            style={{ position: 'absolute', top: 0, left: 0, width: '100%',
                     transform: `translateY(${item.start}px)` }}
            mission={missions[item.index]}
          />
        ))}
      </div>
    </div>
  );
}
```

## Error Boundaries (Required on All Sections)

```tsx
// src/components/ErrorBoundary.tsx — already exists, just use it:
<ErrorBoundary name="MissionsPanel" fallbackTitle="Missions unavailable">
  <MissionsPanel />
</ErrorBoundary>

// For critical path — never wrap entire app in single boundary
// Wrap: each major panel, each graph component, each data-heavy section
```

## Accessibility (WCAG 2.2 AA)

```tsx
// 1. Skip navigation link (first focusable element in layout)
<SkipNav />   // Already in AppShell — don't remove

// 2. ARIA live regions for dynamic updates
<LiveRegion message={statusMessage} politeness="polite" />

// 3. Forms — always label inputs
<label htmlFor="mission-title">Mission title *</label>
<input
  id="mission-title"
  aria-required="true"
  aria-invalid={!!errors.title}
  aria-describedby={errors.title ? "title-error" : undefined}
/>
{errors.title && (
  <p id="title-error" role="alert" className="text-danger text-sm">
    {errors.title.message}
  </p>
)}

// 4. Icon buttons — always have aria-label
<button aria-label="Close dialog" onClick={onClose}>
  <X aria-hidden="true" />
</button>
```

## TypeScript Rules

```typescript
// Always strict mode (configured in tsconfig.json)
// Never use 'any' — use 'unknown' and narrow
// Always type API responses with Zod or interface
// Always type event handlers with React types

type ButtonProps = {
  onClick: React.MouseEventHandler<HTMLButtonElement>;
  children: React.ReactNode;
  disabled?: boolean;
  variant?: 'primary' | 'secondary' | 'ghost';
};

// Never:
const handler = (e: any) => {};         // ❌
const data: any = await api.fetch();    // ❌

// Always:
const handler: React.ChangeEventHandler<HTMLInputElement> = (e) => {};  // ✅
const data: Mission = await api.missions.get(id);  // ✅
```

## Anti-Patterns (Never Generate)

```tsx
// ❌ WRONG — useEffect for data fetching
useEffect(() => {
  fetch('/api/missions').then(r => r.json()).then(setMissions);
}, []);

// ✅ CORRECT — TanStack Query
const { data: missions } = useMissions(orgId);

// ❌ WRONG — inline styles
<div style={{ color: '#3B82F6', padding: '16px' }}>

// ✅ CORRECT — Tailwind classes
<div className="text-accent-blue p-4">

// ❌ WRONG — token in localStorage
localStorage.setItem('token', accessToken);

// ✅ CORRECT — sessionStorage or HttpOnly cookie
sessionStorage.setItem('av_api_key', apiKey);

// ❌ WRONG — calling API directly in component
const missions = await fetch('/api/missions').then(r => r.json());

// ✅ CORRECT — use the api module + TanStack Query hook
const { data: missions } = useMissions(orgId);
```
