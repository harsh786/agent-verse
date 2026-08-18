/**
 * KanbanBoard — drag-and-drop mission/task board with JARVIS dark theme.
 * Columns: Backlog → Planning → Running → Review → Done
 *
 * Skills: frontend-design, emil-design-eng, impeccable-ui, web-guidelines
 */
import { useState, useCallback, useId } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Plus, GripVertical } from 'lucide-react';
import { StatusOrb } from '@/components/ui/StatusOrb';
import { SPRING_FAST, SPRING_PAGE } from '@/components/ui/JARVISPageShell';

export type KanbanStatus = 'backlog' | 'planning' | 'running' | 'review' | 'done';

export interface KanbanCard {
  id: string;
  title: string;
  description?: string;
  status: KanbanStatus;
  priority?: 'low' | 'medium' | 'high';
  agentName?: string;
  tags?: string[];
}

interface KanbanBoardProps {
  cards: KanbanCard[];
  onMove?: (cardId: string, toStatus: KanbanStatus) => void;
  onAdd?: (status: KanbanStatus) => void;
  className?: string;
}

const COLUMNS: { id: KanbanStatus; label: string; color: string }[] = [
  { id: 'backlog',  label: 'Backlog',  color: '#5A7494' },
  { id: 'planning', label: 'Planning', color: '#FFB300' },
  { id: 'running',  label: 'Running',  color: '#00D4FF' },
  { id: 'review',   label: 'Review',   color: '#6366F1' },
  { id: 'done',     label: 'Done',     color: '#00E676' },
];

const PRIORITY_COLOR: Record<string, string> = {
  high: '#FF3366', medium: '#FFB300', low: '#00E676',
};

function KanbanCardItem({ card }: { card: KanbanCard; onDrop?: (id: string, status: KanbanStatus) => void }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={reduce ? { duration: 0 } : SPRING_PAGE}
      draggable
      onDragStart={e => {
        const ev = e as unknown as DragEvent;
        ev.dataTransfer?.setData('cardId', card.id);
      }}
      whileHover={reduce ? {} : { y: -2 }}
      className="group bg-[#162035] border border-white/[0.08] rounded-xl p-3 cursor-grab active:cursor-grabbing select-none hover:border-[rgba(0,212,255,0.20)] hover:shadow-[0_0_16px_rgba(0,212,255,0.10)] transition-[border,box-shadow]"
      role="listitem"
      aria-label={`${card.title}, status: ${card.status}`}
    >
      <div className="flex items-start gap-2">
        <GripVertical size={13} className="text-[#5A7494] mt-0.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" aria-hidden />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-1">
            <StatusOrb status={card.status === 'running' ? 'running' : 'idle'} size={6} />
            <span className="text-[12px] font-semibold text-[#F0F6FF] truncate">{card.title}</span>
            {card.priority && (
              <span className="ml-auto w-1.5 h-1.5 rounded-full shrink-0" style={{ background: PRIORITY_COLOR[card.priority] }} aria-label={`Priority: ${card.priority}`} />
            )}
          </div>
          {card.description && (
            <p className="text-[11px] text-[#5A7494] line-clamp-2">{card.description}</p>
          )}
          {(card.agentName || card.tags?.length) && (
            <div className="flex flex-wrap gap-1 mt-2">
              {card.agentName && (
                <span className="px-1.5 py-0.5 rounded bg-[#00D4FF]/10 text-[#00D4FF] text-[10px]">{card.agentName}</span>
              )}
              {card.tags?.slice(0, 2).map(t => (
                <span key={t} className="px-1.5 py-0.5 rounded bg-white/[0.06] text-[#5A7494] text-[10px]">{t}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

function KanbanColumn({ column, cards, onDrop, onAdd }: {
  column: typeof COLUMNS[number];
  cards: KanbanCard[];
  onDrop: (cardId: string, status: KanbanStatus) => void;
  onAdd?: () => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  const id = useId();

  return (
    <div
      className={`flex flex-col flex-1 min-w-[200px] max-w-xs bg-[#0A0F1A] border rounded-xl overflow-hidden transition-[border-color] ${dragOver ? 'border-[rgba(0,212,255,0.40)]' : 'border-white/[0.06]'}`}
      onDragOver={e => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={e => {
        e.preventDefault();
        setDragOver(false);
        const cardId = e.dataTransfer.getData('cardId');
        if (cardId) onDrop(cardId, column.id);
      }}
      role="list"
      aria-labelledby={id}
    >
      {/* Column header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-white/[0.06]">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ background: column.color }} aria-hidden />
          <span id={id} className="text-[12px] font-semibold" style={{ color: column.color }}>{column.label}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-[#5A7494] tabular-nums">{cards.length}</span>
          {onAdd && (
            <motion.button
              whileTap={{ scale: 0.9 }}
              transition={SPRING_FAST}
              onClick={onAdd}
              aria-label={`Add card to ${column.label}`}
              className="p-0.5 rounded text-[#5A7494] hover:text-[#A0B4CC] hover:bg-white/[0.06]"
            >
              <Plus size={13} aria-hidden />
            </motion.button>
          )}
        </div>
      </div>

      {/* Cards */}
      <div className="flex-1 p-2 flex flex-col gap-2 overflow-y-auto min-h-[80px]">
        <AnimatePresence mode="popLayout">
          {cards.map(card => (
            <KanbanCardItem key={card.id} card={card} onDrop={onDrop} />
          ))}
        </AnimatePresence>
        {dragOver && cards.length === 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="h-16 rounded-xl border-2 border-dashed border-[rgba(0,212,255,0.30)] flex items-center justify-center"
          >
            <span className="text-[11px] text-[#00D4FF]">Drop here</span>
          </motion.div>
        )}
      </div>
    </div>
  );
}

export function KanbanBoard({ cards, onMove, onAdd, className = '' }: KanbanBoardProps) {
  const [localCards, setLocalCards] = useState<KanbanCard[]>(cards);

  const handleDrop = useCallback((cardId: string, toStatus: KanbanStatus) => {
    setLocalCards(prev => prev.map(c => c.id === cardId ? { ...c, status: toStatus } : c));
    onMove?.(cardId, toStatus);
  }, [onMove]);

  return (
    <div className={`flex gap-3 overflow-x-auto pb-2 ${className}`} role="region" aria-label="Kanban board">
      {COLUMNS.map(col => (
        <KanbanColumn
          key={col.id}
          column={col}
          cards={localCards.filter(c => c.status === col.id)}
          onDrop={handleDrop}
          onAdd={onAdd ? () => onAdd(col.id) : undefined}
        />
      ))}
    </div>
  );
}
