# Adaptive Connector Result Rendering Design

## Context

Goal execution currently displays tool output in the execution stream through generic string or JSON formatting. This makes successful connector results hard to read, especially when the output is a list of records such as Jira issues, GitHub pull requests, Linear tickets, Sentry issues, Confluence pages, or Slack messages.

The UI should render connector results like modern coding agents: concise status, readable structured output, and raw diagnostics only when needed. The renderer must not be Jira-specific. It should infer the best presentation from the output shape.

## Goals

- Render connector/tool outputs in a human-readable format by default.
- Support any connector without custom UI per connector.
- Preserve raw output and diagnostics for debugging.
- Make zero-result and failed-tool cases explicit and actionable.
- Keep existing event stream and result artifact architecture intact.

## Non-Goals

- Build bespoke views for every connector.
- Use the LLM to count, paginate, or format authoritative structured data.
- Remove raw JSON inspection.
- Redesign the full goal detail page.

## Recommended Approach

Add a shape-based result normalizer and renderer.

The normalizer inspects tool output and emits a display model independent of connector name. The UI renders that display model using a small set of generic views:

- Summary panel for status, counts, source, query, and completeness.
- Table view for arrays of similarly-shaped records.
- Card view for small sets of rich records in a later enhancement.
- Text view for narrative/plain output.
- Diagnostic panel for failures and zero-result responses.
- Collapsible JSON inspector for raw output.

## Data Flow

1. Backend tool call returns structured output through `tool_call_complete` or result artifact evidence.
2. Frontend receives the event or artifact.
3. A frontend normalizer converts unknown output into an `AdaptiveResultViewModel`.
4. The result renderer displays the best view based on the model.
5. Raw output remains available in a collapsible debug panel.

## Display Model

The frontend should derive a display model with this conceptual shape:

```ts
type AdaptiveResultViewModel = {
  status: 'success' | 'failed' | 'empty' | 'partial';
  title: string;
  summary?: string;
  metrics: Array<{ label: string; value: string | number | boolean }>;
  primaryView: 'table' | 'text' | 'json' | 'diagnostic';
  table?: {
    columns: Array<{ key: string; label: string; type: 'text' | 'link' | 'badge' | 'datetime' | 'number' }>;
    rows: Record<string, unknown>[];
  };
  text?: string;
  diagnostics: Array<{ label: string; value: string }>;
  raw: unknown;
};
```

This model is frontend-derived initially. Backend result artifacts can later emit the same shape directly.

## Shape Detection Rules

- If output contains `error` or tool success is false, render a diagnostic panel.
- If output has count fields such as `total`, `returned`, `max_results`, `is_complete`, or `next_page_token`, surface them as metrics.
- If output contains an array of objects under common keys like `issues`, `items`, `results`, `values`, `nodes`, `pull_requests`, `tickets`, `tasks`, `users`, `files`, or `messages`, render a table.
- If the array has fewer than five rich records with long summaries/descriptions, the first implementation still renders a compact table. Card rendering is deferred until the table, text, diagnostic, and JSON paths are stable.
- If output is a string, number, or boolean, render text.
- If no known structure is detected, render a JSON inspector.

## Column Inference

For table-like records, infer columns in this order:

- Identity fields: `key`, `id`, `number`, `identifier`, `name`, `title`, `summary`.
- State fields: `status`, `state`, `priority`, `type`, `issue_type`.
- Ownership fields: `assignee`, `owner`, `author`, `reporter`, `created_by`.
- Time fields: `created`, `created_at`, `updated`, `updated_at`, `last_seen`.
- Link fields: `url`, `web_url`, `html_url`, `self`.

Limit default visible columns to a readable maximum, with raw JSON available for the full object.

## Zero-Result Handling

Zero results should not silently look like success. The UI should show:

- Query or filter used when available.
- Connector/tool name.
- Whether the tool call itself succeeded.
- Returned count and total count.
- A short next-action hint: check query, permissions, project scope, or credentials.

## Error Handling

Failed tool calls should render as diagnostics instead of raw JSON. Include:

- Tool name.
- Connector/server ID when available.
- Error message.
- Query or arguments summary when safe to display.
- Raw output collapsed.

## UI Placement

Use the existing goal detail structure:

- Results tab: render the adaptive result canvas for terminal goals and result artifacts.
- Execution tab: render each `tool_call_complete` expanded row using the same adaptive renderer instead of plain JSON text.
- Events tab: keep raw events available for low-level debugging.

## Testing Strategy

Add frontend unit tests for the normalizer and renderer using representative outputs:

- Jira-style issues output.
- GitHub-style pull requests/issues output.
- Linear-style nodes output.
- Slack-style messages output.
- Plain text output.
- Error output.
- Empty output with query metadata.
- Unknown nested JSON fallback.

Add component tests to verify expanded execution rows display tables/diagnostics instead of raw JSON blobs.

## Implementation Decisions

- The first implementation derives the adaptive display model in the frontend from existing events and result artifacts.
- The first implementation includes table, text, diagnostic, metric summary, and JSON fallback views.
- Card rendering is deferred to a later enhancement.
- Backend result artifacts do not need to change for the first implementation.

## Recommendation

Implement frontend-derived adaptive rendering first. It is the smallest change, works with existing backend events, and improves all connectors without changing the tool protocol. Backend result artifacts can be aligned later once the display model proves stable.
