/**
 * DepartmentTree — collapsible department hierarchy with spring animations.
 *
 * Skills:
 *   - emil-design-eng: layout spring on expand/collapse, stagger children
 *   - impeccable-ui:   dept name dominant, agent count secondary
 *   - web-guidelines:  aria-expanded, role=treeitem, keyboard nav
 *   - ui-ux-pro-max:   reduced motion, 44px targets
 */
import React, { useState, useCallback } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { ChevronRight, Building2, Users } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useDepartments } from '../hooks/useOrg';
import type { OrgDepartment } from '../types';

interface DepartmentTreeProps {
  orgId:        string;
  onDeptSelect?: (dept: OrgDepartment) => void;
  className?:   string;
}

export function DepartmentTree({ orgId, onDeptSelect, className }: DepartmentTreeProps) {
  const { data: depts, isLoading } = useDepartments(orgId);
  const rootDepts = (depts ?? []).filter(d => !d.parent_dept_id);

  if (isLoading) return <SkeletonTree />;
  if (rootDepts.length === 0) return <EmptyDepts />;

  return (
    <nav
      aria-label="Department hierarchy"
      className={cn('space-y-1', className)}
    >
      <ul role="tree" aria-label="Departments">
        {rootDepts.map(dept => (
          <DeptNode
            key={dept.id}
            dept={dept}
            allDepts={depts ?? []}
            depth={0}
            onSelect={onDeptSelect}
          />
        ))}
      </ul>
    </nav>
  );
}

function DeptNode({
  dept, allDepts, depth, onSelect,
}: {
  dept:     OrgDepartment;
  allDepts: OrgDepartment[];
  depth:    number;
  onSelect?: (d: OrgDepartment) => void;
}) {
  const reduce   = useReducedMotion();
  const [open, setOpen] = useState(depth === 0);
  const children = allDepts.filter(d => d.parent_dept_id === dept.id);
  const hasChildren = children.length > 0;

  const toggle = useCallback(() => setOpen(v => !v), []);
  const handleKey = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    if (e.key === 'ArrowRight' && !open) setOpen(true);
    if (e.key === 'ArrowLeft'  && open)  setOpen(false);
  }, [open, toggle]);

  return (
    <li role="treeitem" aria-expanded={hasChildren ? open : undefined}>
      <button
        onClick={() => { toggle(); onSelect?.(dept); }}
        onKeyDown={handleKey}
        aria-label={`${dept.name}${hasChildren ? `, ${open ? 'collapse' : 'expand'}` : ''}`}
        style={{ touchAction: 'manipulation', paddingLeft: `${depth * 16 + 8}px` }}
        className={cn(
          'w-full flex items-center gap-2 pr-3 py-2 rounded-lg text-left',
          'text-[13px] font-medium text-[#94A3B8]',
          'hover:bg-[#1A1F2E] hover:text-[#F1F5F9]',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
          'transition-colors duration-150',
          'min-h-[36px]',
        )}
      >
        {/* Chevron (rotates on open — spring) */}
        {hasChildren ? (
          <motion.span
            animate={{ rotate: open ? 90 : 0 }}
            transition={reduce ? { duration: 0 } : { type: 'spring', stiffness: 400, damping: 30 }}
            className="shrink-0"
          >
            <ChevronRight className="h-3.5 w-3.5 text-[#475569]" aria-hidden />
          </motion.span>
        ) : (
          <span className="w-3.5 shrink-0" />
        )}

        <Building2 className="h-3.5 w-3.5 shrink-0 text-blue-400/60" aria-hidden />

        {/* Dept name — primary (impeccable-ui) */}
        <span className="flex-1 min-w-0 truncate">{dept.name}</span>

        {/* Agent count — secondary */}
        {dept.agent_count != null && dept.agent_count > 0 && (
          <span className="flex items-center gap-1 shrink-0 text-[11px] text-[#475569]">
            <Users className="h-3 w-3" aria-hidden />
            <span className="tabular-nums">{dept.agent_count}</span>
          </span>
        )}
      </button>

      {/* Children (spring expand/collapse) */}
      <AnimatePresence initial={false}>
        {hasChildren && open && (
          <motion.ul
            role="group"
            initial={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={reduce ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
            exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={reduce ? { duration: 0.1 } : { type: 'spring', stiffness: 300, damping: 28 }}
            style={{ overflow: 'hidden' }}
          >
            {children.map((child, i) => (
              <div
                key={child.id}
                className="jarvis-rise-in"
                style={{ animationDelay: `${Math.min(i, 8) * 0.04}s` }}
              >
                <DeptNode
                  dept={child}
                  allDepts={allDepts}
                  depth={depth + 1}
                  onSelect={onSelect}
                />
              </div>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </li>
  );
}

function SkeletonTree() {
  return (
    <div className="space-y-1.5 animate-pulse">
      {[1, 2, 3].map(i => (
        <div key={i} className="flex items-center gap-2 px-2 py-2">
          <div className="h-3.5 w-3.5 rounded bg-[#252B3B] shrink-0" />
          <div className="h-3.5 rounded bg-[#252B3B]" style={{ width: `${60 + i * 15}%` }} />
        </div>
      ))}
    </div>
  );
}

function EmptyDepts() {
  return (
    <p className="text-[13px] text-[#475569] py-2 px-2">
      No departments yet
    </p>
  );
}
