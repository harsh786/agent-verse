/**
 * Debate Viewer — renders structured agent debates.
 */

interface DebateViewerProps {
  debates: Record<string, unknown>[];
}

export function DebateViewer({ debates }: DebateViewerProps) {
  if (debates.length === 0) {
    return (
      <div className="text-sm text-gray-400 text-center py-6">
        No debates recorded yet.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {debates.map((debate, i) => (
        <div key={i} className="border rounded-lg p-3 text-sm">
          <pre className="text-xs text-gray-600 overflow-auto whitespace-pre-wrap">
            {JSON.stringify(debate, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  );
}
