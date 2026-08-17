---
description: "Generate a complete React feature: page + components + hooks + API + types + tests"
---

# Generate New Frontend Feature

Generate a world-class React feature for AgentVerse following all conventions.

## Feature Details
- **Feature name**: {{feature_name}}
- **Route**: {{route_path}}
- **Description**: {{description}}
- **Data from backend**: {{api_endpoint}}

## What to Generate

### 1. `src/features/{{feature_name}}/types.ts`
- All TypeScript interfaces for domain objects
- API response/request types
- No `any` — strict typing

### 2. `src/features/{{feature_name}}/api.ts`
- All API calls using the shared `request<T>()` from `src/lib/api/client.ts`
- Full TypeScript types on all functions
- No fetch/axios directly in components

### 3. `src/features/{{feature_name}}/hooks/use{{FeatureName}}.ts`
- TanStack Query `useQuery` for data fetching
- `useMutation` for writes (with optimistic updates)
- Query keys factory pattern
- `staleTime`, `gcTime` configured
- Cache invalidation on mutations
- SSE connection hook if needed

### 4. `src/features/{{feature_name}}/{{FeatureName}}Page.tsx`
- Route-level component (will be lazy-loaded by router)
- `<ErrorBoundary>` wrapping main content
- `<Suspense>` with skeleton fallback
- Empty state when no data
- Proper heading hierarchy (h1 → h2 → h3)
- `data-testid` on key elements

### 5. `src/features/{{feature_name}}/components/{{FeatureName}}List.tsx`
- `useVirtualizer` from `@tanstack/react-virtual` (required for lists)
- `React.memo` on list item component
- Loading state (skeleton)
- Empty state
- Error state

### 6. `src/features/{{feature_name}}/components/{{FeatureName}}Form.tsx`
- All inputs labeled (for accessibility)
- `aria-required`, `aria-invalid`, `aria-describedby` on fields
- Error messages with `role="alert"`
- Disabled state during submission
- Optimistic update feedback

### 7. `src/features/{{feature_name}}/__tests__/{{FeatureName}}.test.tsx`
- Happy path rendering
- Empty state
- Error boundary trigger
- Form submission (with MSW handler)
- Optimistic update verification
- `a11y` check with axe-core

### 8. `src/features/{{feature_name}}/index.ts`
- Export: page component, types, hooks

## Constraints
- NEVER `useEffect` for data fetching
- NEVER `localStorage` for tokens
- NEVER `any` TypeScript type
- NEVER inline styles — use Tailwind
- ALWAYS use TanStack Query for server state
- ALWAYS use Zustand for UI state (not useState for global state)
- ALWAYS `React.memo` on list item components
- ALWAYS `useVirtualizer` if list can exceed 50 items
- ALWAYS error boundary around the page
- ALWAYS proper ARIA labels on interactive elements
