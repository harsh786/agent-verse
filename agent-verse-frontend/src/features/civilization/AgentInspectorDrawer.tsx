/**
 * Agent Inspector Drawer — shows detailed agent state when a node is clicked.
 */

interface AgentInspectorDrawerProps {
  civilizationId: string;
  agentId: string | null;
  onClose: () => void;
}

export function AgentInspectorDrawer({ agentId, onClose }: AgentInspectorDrawerProps) {
  if (!agentId) return null;

  return (
    <div className="fixed inset-y-0 right-0 w-96 bg-white border-l shadow-xl z-50 flex flex-col">
      <div className="flex items-center justify-between p-4 border-b">
        <h2 className="font-semibold text-sm">Agent Inspector</h2>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg leading-none">
          &times;
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        <div className="text-xs text-gray-500 font-mono break-all">{agentId}</div>
        <div className="mt-4 text-sm text-gray-400">
          Detailed agent state, memory, and tool call history will be displayed here.
        </div>
      </div>
    </div>
  );
}
