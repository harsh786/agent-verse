/**
 * OrgChart — interactive SVG org chart showing agent hierarchy within an org.
 * Uses D3-style tree layout rendered with React + framer-motion.
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { useId, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { Bot, Building2, ChevronDown, ChevronRight, User, Zap } from 'lucide-react';
import { StatusOrb } from '@/components/ui/StatusOrb';
import { SPRING_FAST } from '@/components/ui/JARVISPageShell';

export interface OrgNode {
  id: string;
  name: string;
  role: 'org' | 'agent' | 'team' | 'human';
  status?: string;
  children?: OrgNode[];
  metadata?: Record<string, string | number>;
}

interface OrgChartProps {
  root: OrgNode;
  className?: string;
  onNodeClick?: (node: OrgNode) => void;
}

const ROLE_CONFIG: Record<string, { icon: typeof Bot; color: string; bg: string }> = {
  org:    { icon: Building2, color: '#00D4FF', bg: 'rgba(0,212,255,0.15)' },
  agent:  { icon: Bot,       color: '#6366F1', bg: 'rgba(99,102,241,0.15)' },
  team:   { icon: Zap,       color: '#FFB300', bg: 'rgba(255,179,0,0.15)' },
  human:  { icon: User,      color: '#00E676', bg: 'rgba(0,230,118,0.15)' },
};

function OrgTreeNode({ node, depth = 0, onNodeClick }: { node: OrgNode; depth?: number; onNodeClick?: (n: OrgNode) => void }) {
  const reduce = useReducedMotion();
  const [expanded, setExpanded] = useState(depth < 2);
  const cfg = ROLE_CONFIG[node.role] ?? ROLE_CONFIG.agent;
  const Icon = cfg.icon;
  const hasChildren = (node.children?.length ?? 0) > 0;

  return (
    <div className="flex flex-col items-center">
      {/* Node */}
      <div
        className="jarvis-rise-in relative"
        style={{ animationDelay: `${Math.min(depth, 8) * 0.06}s` }}
      >
        <button
          onClick={() => { onNodeClick?.(node); if (hasChildren) setExpanded(x => !x); }}
          aria-expanded={hasChildren ? expanded : undefined}
          aria-label={`${node.name} (${node.role})`}
          style={{ touchAction: 'manipulation' }}
          className="group flex flex-col items-center gap-1.5 px-3 py-2 rounded-xl border border-white/[0.08] bg-[#0F1826] hover:border-[rgba(0,212,255,0.25)] hover:bg-[#162035] transition-[background,border] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/60 min-w-[80px]"
        >
          <div className="relative">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: cfg.bg }}>
              <Icon size={18} style={{ color: cfg.color }} aria-hidden />
            </div>
            {node.status && (
              <StatusOrb status={node.status} size={8} className="absolute -top-0.5 -right-0.5" />
            )}
          </div>
          <span className="text-[11px] font-semibold text-[#F0F6FF] text-center max-w-[80px] truncate" title={node.name}>
            {node.name}
          </span>
          <span className="text-[9px] capitalize" style={{ color: cfg.color }}>{node.role}</span>

          {hasChildren && (
            <div className="text-[#5A7494]">
              {expanded ? <ChevronDown size={11} aria-hidden /> : <ChevronRight size={11} aria-hidden />}
            </div>
          )}
        </button>
      </div>

      {/* Children */}
      {hasChildren && expanded && (
        <motion.div
          initial={reduce ? { opacity: 1 } : { opacity: 1, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={reduce ? { opacity: 0 } : { opacity: 0, height: 0 }}
          transition={SPRING_FAST}
          className="flex flex-col items-center"
        >
          {/* Vertical connector */}
          <div className="w-px h-5 bg-white/[0.10]" aria-hidden />
          {/* Horizontal bar */}
          {(node.children?.length ?? 0) > 1 && (
            <div className="relative h-px bg-white/[0.10]" style={{ width: `${(node.children!.length - 1) * 112}px` }} aria-hidden />
          )}
          <div className="flex gap-4" role="group" aria-label={`${node.name} children`}>
            {node.children?.map(child => (
              <div key={child.id} className="flex flex-col items-center">
                <div className="w-px h-4 bg-white/[0.10]" aria-hidden />
                <OrgTreeNode node={child} depth={depth + 1} onNodeClick={onNodeClick} />
              </div>
            ))}
          </div>
        </motion.div>
      )}
    </div>
  );
}

export function OrgChart({ root, className = '', onNodeClick }: OrgChartProps) {
  const id = useId();
  return (
    <div className={`overflow-auto ${className}`} role="tree" aria-label="Organization chart" id={id}>
      <div className="flex justify-center py-6 min-w-max px-8">
        <OrgTreeNode node={root} onNodeClick={onNodeClick} />
      </div>
    </div>
  );
}

export { OrgChart as InteractiveOrgChart };
