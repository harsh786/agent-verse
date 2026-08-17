---
applyTo: "agent-verse-frontend/src/**/*.{ts,tsx}"
---

# Frontend Custom Hooks — AgentVerse Patterns

## Hook Naming & Location

```
src/
  hooks/                              # Global shared hooks
    useDebounce.ts
    useIntersectionObserver.ts
    useLocalStorage.ts

  features/<domain>/
    hooks/
      use<Domain>.ts                  # TanStack Query data hooks
      use<Domain>Store.ts             # Zustand store slice
      use<Domain>SSE.ts               # SSE connection hooks
```

## TanStack Query Hook Pattern (Data Fetching)

```typescript
// src/features/missions/hooks/useMissions.ts
import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from '@tanstack/react-query';
import { api } from '../api';
import type { Mission, CreateMissionRequest } from '../types';

// ── Query key factory (centralise all cache keys here) ──────────────────────
export const missionKeys = {
  all:         (orgId: string) => ['missions', orgId]           as const,
  detail:      (id: string)    => ['mission', id]               as const,
  byStatus:    (orgId: string, status: string) =>
                                  ['missions', orgId, status]   as const,
};

// ── List hook (infinite scroll / cursor pagination) ─────────────────────────
export function useMissions(orgId: string) {
  return useInfiniteQuery({
    queryKey:           missionKeys.all(orgId),
    queryFn:            ({ pageParam }) => api.missions.list(orgId, { cursor: pageParam }),
    initialPageParam:   undefined as string | undefined,
    getNextPageParam:   (page) => page.cursor ?? undefined,
    staleTime:          30_000,
    gcTime:             5 * 60_000,
  });
}

// ── Single entity hook ───────────────────────────────────────────────────────
export function useMission(missionId: string) {
  return useQuery({
    queryKey: missionKeys.detail(missionId),
    queryFn:  () => api.missions.get(missionId),
    enabled:  !!missionId,
  });
}

// ── Create mutation (with optimistic update) ────────────────────────────────
export function useCreateMission(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateMissionRequest) => api.missions.create(orgId, req),

    onMutate: async (req) => {
      // 1. Cancel any in-flight queries
      await qc.cancelQueries({ queryKey: missionKeys.all(orgId) });
      // 2. Snapshot current data (for rollback)
      const prev = qc.getQueryData(missionKeys.all(orgId));
      // 3. Optimistically add to cache
      qc.setQueryData(missionKeys.all(orgId), (old: any) => ({
        ...old,
        pages: old?.pages?.map((page: any, i: number) =>
          i === 0
            ? { ...page, data: [{ id: `temp-${Date.now()}`, ...req }, ...page.data] }
            : page
        ) ?? [{ data: [{ id: `temp-${Date.now()}`, ...req }], cursor: null }],
      }));
      return { prev };
    },

    onError: (_err, _req, context) => {
      // Rollback on failure
      qc.setQueryData(missionKeys.all(orgId), context?.prev);
    },

    onSettled: () => {
      // Always refetch to ensure sync with server
      qc.invalidateQueries({ queryKey: missionKeys.all(orgId) });
    },
  });
}
```

## SSE Hook Pattern (Real-Time Streaming)

```typescript
// src/features/missions/hooks/useMissionStream.ts
import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { MissionEvent } from '../types';
import { missionKeys } from './useMissions';

export function useMissionStream(missionId: string) {
  const qc = useQueryClient();
  const esRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const reconnectDelay = useRef(1000);

  useEffect(() => {
    let stopped = false;

    function connect() {
      if (stopped) return;
      const es = new EventSource(`/api/v1/missions/${missionId}/stream`, {
        withCredentials: true,
      });
      esRef.current = es;

      es.onopen = () => {
        reconnectDelay.current = 1000;   // reset backoff on success
      };

      es.addEventListener('mission_event', (e) => {
        const event: MissionEvent = JSON.parse(e.data);
        // Update cache in real-time (no refetch needed)
        qc.setQueryData(missionKeys.detail(missionId), (old: any) =>
          old ? { ...old, status: event.status, progress: event.progress } : old
        );
      });

      es.onerror = () => {
        es.close();
        if (!stopped) {
          // Exponential backoff reconnect
          reconnectTimerRef.current = setTimeout(() => {
            reconnectDelay.current = Math.min(reconnectDelay.current * 2, 30_000);
            connect();
          }, reconnectDelay.current);
        }
      };
    }

    connect();

    return () => {
      stopped = true;
      clearTimeout(reconnectTimerRef.current);
      esRef.current?.close();
    };
  }, [missionId, qc]);
}
```

## WebSocket Hook Pattern (Collaboration)

```typescript
// src/lib/ws/useCollabSocket.ts
import { useEffect, useRef, useCallback } from 'react';

type WSMessage = { type: string; payload: unknown };

export function useCollabSocket(orgId: string, onMessage: (msg: WSMessage) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelay = useRef(1000);
  const queueRef = useRef<string[]>([]);    // buffer messages when disconnected

  const send = useCallback((msg: WSMessage) => {
    const data = JSON.stringify(msg);
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    } else {
      queueRef.current.push(data);   // queue for when reconnected
    }
  }, []);

  useEffect(() => {
    let stopped = false;
    let delay = 1000;

    function connect() {
      if (stopped) return;
      const ws = new WebSocket(`${location.origin.replace('http', 'ws')}/ws/collab/${orgId}`);
      wsRef.current = ws;

      ws.onopen = () => {
        delay = 1000;
        // Flush queued messages
        while (queueRef.current.length > 0) {
          ws.send(queueRef.current.shift()!);
        }
      };

      ws.onmessage = (e) => onMessage(JSON.parse(e.data));

      ws.onclose = (e) => {
        if (!stopped && e.code !== 1000) {
          setTimeout(() => { delay = Math.min(delay * 2, 30_000); connect(); }, delay);
        }
      };
    }

    connect();
    return () => { stopped = true; wsRef.current?.close(1000); };
  }, [orgId, onMessage]);

  return { send };
}
```

## Zustand Store Hook Pattern (UI State)

```typescript
// src/features/knowledge/hooks/useGraphStore.ts
import { create } from 'zustand';
import { devtools, subscribeWithSelector } from 'zustand/middleware';

interface GraphStore {
  // State
  selectedNodeId:   string | null;
  hoveredNodeId:    string | null;
  zoomLevel:        number;
  layout:           'force' | 'radial' | 'tree';
  activeFilters:    Set<string>;

  // Actions (always functions, never mutate state directly)
  selectNode:       (id: string | null) => void;
  hoverNode:        (id: string | null) => void;
  setZoom:          (level: number) => void;
  setLayout:        (layout: GraphStore['layout']) => void;
  toggleFilter:     (filter: string) => void;
  resetView:        () => void;
}

const initialState = {
  selectedNodeId: null,
  hoveredNodeId:  null,
  zoomLevel:      1,
  layout:         'force' as const,
  activeFilters:  new Set<string>(),
};

export const useGraphStore = create<GraphStore>()(
  devtools(
    subscribeWithSelector((set) => ({
      ...initialState,
      selectNode:   (id) => set({ selectedNodeId: id }),
      hoverNode:    (id) => set({ hoveredNodeId: id }),
      setZoom:      (level) => set({ zoomLevel: Math.max(0.1, Math.min(5, level)) }),
      setLayout:    (layout) => set({ layout }),
      toggleFilter: (filter) => set((s) => {
        const next = new Set(s.activeFilters);
        next.has(filter) ? next.delete(filter) : next.add(filter);
        return { activeFilters: next };
      }),
      resetView: () => set(initialState),
    })),
    { name: 'graph-store' }
  )
);

// Selector hooks (memoized — prevent unnecessary re-renders)
export const useSelectedNode = () => useGraphStore((s) => s.selectedNodeId);
export const useGraphLayout  = () => useGraphStore((s) => s.layout);
```

## Utility Hook Patterns

```typescript
// ── Debounce hook (search inputs) ───────────────────────────────────────────
export function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

// ── Intersection observer (lazy load, infinite scroll trigger) ───────────────
export function useIntersectionObserver(
  ref: React.RefObject<Element>,
  options?: IntersectionObserverInit
) {
  const [isIntersecting, setIsIntersecting] = useState(false);
  useEffect(() => {
    if (!ref.current) return;
    const obs = new IntersectionObserver(
      ([entry]) => setIsIntersecting(entry.isIntersecting),
      options
    );
    obs.observe(ref.current);
    return () => obs.disconnect();
  }, [ref, options]);
  return isIntersecting;
}

// ── useEventListener (keyboard shortcuts, global events) ────────────────────
export function useEventListener<K extends keyof WindowEventMap>(
  event: K,
  handler: (e: WindowEventMap[K]) => void,
  element: EventTarget = window
) {
  const handlerRef = useRef(handler);
  useLayoutEffect(() => { handlerRef.current = handler; });
  useEffect(() => {
    const fn = (e: Event) => handlerRef.current(e as WindowEventMap[K]);
    element.addEventListener(event, fn);
    return () => element.removeEventListener(event, fn);
  }, [event, element]);
}
```

## Hook Rules (Non-Negotiable)

1. **Data fetching**: `useQuery` or `useInfiniteQuery` — never `useState + useEffect`
2. **Mutations**: `useMutation` with optimistic updates + rollback
3. **Global UI state**: Zustand store — never prop-drilling or Context for global state
4. **Local UI state**: `useState` — only for component-local state (modal open, input value)
5. **SSE connections**: always clean up in `useEffect` return (prevent memory leaks)
6. **Callbacks passed to children**: always `useCallback` to prevent re-renders
7. **Expensive calculations**: always `useMemo`
8. **No hooks in conditions or loops** — always at component top level
