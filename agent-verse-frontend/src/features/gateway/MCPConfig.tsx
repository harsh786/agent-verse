/**
 * MCPConfig — MCP server configuration and tool toggles.
 * JARVIS motion: JARVISPageShell + JARVISStagger tool list.
 */
import { useState } from 'react';
import { Cpu, Copy, CheckCircle2, ToggleLeft, ToggleRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { Badge } from '@/components/ui/badge';

const DEFAULT_TOOLS = [
  { id: 'ask_organization',       label: 'ask_organization',       desc: 'Ask the org anything',          risk: 'read',  enabled: true  },
  { id: 'start_mission',          label: 'start_mission',          desc: 'Create a new mission',          risk: 'write', enabled: true  },
  { id: 'get_status',             label: 'get_status',             desc: 'Get org health + active missions',risk: 'read', enabled: true  },
  { id: 'list_missions',          label: 'list_missions',          desc: 'List missions by status',       risk: 'read',  enabled: true  },
  { id: 'get_mission_result',     label: 'get_mission_result',     desc: 'Get completed mission results', risk: 'read',  enabled: true  },
  { id: 'list_pending_approvals', label: 'list_pending_approvals', desc: 'List items needing approval',   risk: 'read',  enabled: true  },
  { id: 'approve',                label: 'approve',                desc: 'Approve a pending action',      risk: 'write', enabled: true  },
  { id: 'search_knowledge',       label: 'search_knowledge',       desc: 'Search org knowledge base',     risk: 'read',  enabled: true  },
  { id: 'search_memory',          label: 'search_memory',          desc: 'Search org memory',             risk: 'read',  enabled: true  },
  { id: 'pause_organization',     label: 'pause_organization',     desc: 'Pause all autonomous work',     risk: 'admin', enabled: false },
];

interface MCPConfigProps { orgId: string; }

export function MCPConfig({ orgId }: MCPConfigProps) {
  const mcpUrl   = `wss://${window.location.host}/v1/mcp/${orgId}`;
  const [tools, setTools] = useState(DEFAULT_TOOLS);
  const [copied, setCopied] = useState(false);

  const toggleTool = (id: string) => {
    setTools(ts => ts.map(t => t.id === id ? { ...t, enabled: !t.enabled } : t));
  };

  const copyConfig = () => {
    const config = {
      mcpServers: {
        'agentverse-org': {
          command: 'npx',
          args: ['-y', '@agentverse/mcp-proxy'],
          env: { AGENTVERSE_ORG_URL: mcpUrl, AGENTVERSE_API_KEY: '<your-api-key>' },
        },
      },
    };
    navigator.clipboard.writeText(JSON.stringify(config, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const RISK_BADGE: Record<string, string> = {
    read:  'border-emerald-500/30 text-emerald-400',
    write: 'border-yellow-500/30 text-yellow-400',
    admin: 'border-red-500/30 text-red-400',
  };

  return (
    <JARVISPageShell className="flex flex-col gap-5 max-w-2xl">
      {/* Header */}
      <div>
        <h1 className="text-base font-semibold text-[#F1F5F9] flex items-center gap-2">
          <Cpu className="h-4 w-4 text-[#00D4FF]" aria-hidden />
          MCP Server
        </h1>
        <p className="text-[11px] text-[#475569] mt-0.5">
          Expose this org as MCP tools for Claude Desktop, Cursor, and any MCP client.
        </p>
      </div>

      {/* Endpoint */}
      <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4 space-y-3">
        <p className="text-xs text-[#475569] uppercase tracking-wider">MCP Endpoint</p>
        <div className="flex items-center gap-2 bg-[#1A1F2E] rounded-lg px-3 py-2">
          <code className="text-xs text-[#94A3B8] flex-1 truncate font-mono">{mcpUrl}</code>
          <button onClick={copyConfig} aria-label="Copy Claude Desktop config" style={{ touchAction: 'manipulation' }} className="flex items-center gap-1.5 px-2 py-1 rounded text-xs text-[#475569] hover:text-[#94A3B8] transition-colors">
            {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
            {copied ? 'Copied' : 'Copy config'}
          </button>
        </div>
        <p className="text-[11px] text-[#475569]">Auth: <code className="text-[#94A3B8]">Authorization: Bearer {'<api-key>'}</code></p>
      </div>

      {/* Tool toggles */}
      <div>
        <p className="text-xs text-[#475569] uppercase tracking-wider mb-3">Exposed Tools</p>
        <JARVISStagger className="space-y-2">
          {tools.map(tool => (
            <JARVISStaggerItem key={tool.id}>
              <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-[#0F1623] border border-[#1E2535]">
                <button
                  onClick={() => toggleTool(tool.id)}
                  aria-pressed={tool.enabled}
                  aria-label={`${tool.enabled ? 'Disable' : 'Enable'} ${tool.label}`}
                  style={{ touchAction: 'manipulation' }}
                  className="flex-shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50 rounded"
                >
                  {tool.enabled
                    ? <ToggleRight className="h-5 w-5 text-[#00D4FF]" aria-hidden />
                    : <ToggleLeft  className="h-5 w-5 text-[#475569]" aria-hidden />}
                </button>
                <div className="flex-1 min-w-0">
                  <p className={cn('text-sm font-mono', tool.enabled ? 'text-[#F1F5F9]' : 'text-[#475569]')}>
                    {tool.label}
                  </p>
                  <p className="text-[11px] text-[#475569] truncate">{tool.desc}</p>
                </div>
                <Badge variant="outline" className={cn('text-[10px] flex-shrink-0', RISK_BADGE[tool.risk])}>
                  {tool.risk}
                </Badge>
              </div>
            </JARVISStaggerItem>
          ))}
        </JARVISStagger>
      </div>

      {/* Claude Desktop snippet */}
      <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4 space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs text-[#475569]">Claude Desktop config snippet</p>
          <button onClick={copyConfig} style={{ touchAction: 'manipulation' }} className="text-xs text-[#00D4FF] hover:underline flex items-center gap-1">
            {copied ? 'Copied!' : 'Copy'}
          </button>
        </div>
        <pre className="text-[11px] text-[#94A3B8] font-mono overflow-x-auto whitespace-pre-wrap bg-[#1A1F2E] rounded p-3">
{`{
  "mcpServers": {
    "agentverse-org": {
      "command": "npx",
      "args": ["-y", "@agentverse/mcp-proxy"],
      "env": {
        "AGENTVERSE_ORG_URL": "${mcpUrl}",
        "AGENTVERSE_API_KEY": "<your-api-key>"
      }
    }
  }
}`}
        </pre>
      </div>
    </JARVISPageShell>
  );
}

export default MCPConfig;
