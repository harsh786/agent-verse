/**
 * CursorPresence — shows who else is viewing this org in real-time.
 * Uses the existing WebSocket collaboration channel.
 *
 * Skills:
 *   - frontend-design:   coloured avatar stack with JARVIS glow
 *   - emil-design-eng:   spring enter/exit per avatar (stagger)
 *   - impeccable-ui:     compact, tertiary visual weight
 *   - web-guidelines:    aria-label, tooltip accessible, reduced-motion
 */
import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils';

interface Presence {
  userId:   string;
  name:     string;
  color:    string;
  section?: string;
}

interface CursorPresenceProps {
  orgId:    string;
  className?: string;
}

// Stable colour palette per user index (deterministic from userId hash)
const COLORS = [
  '#3B82F6', // blue
  '#8B5CF6', // violet
  '#06B6D4', // cyan
  '#10B981', // emerald
  '#F59E0B', // amber
  '#EC4899', // pink
];

function hashColor(userId: string): string {
  let hash = 0;
  for (let i = 0; i < userId.length; i++) {
    hash = (hash * 31 + userId.charCodeAt(i)) | 0;
  }
  return COLORS[Math.abs(hash) % COLORS.length];
}

const AVATAR_SPRING = { type: 'spring', stiffness: 400, damping: 30 } as const;

export function CursorPresence({ orgId, className }: CursorPresenceProps) {
  const reduce = useReducedMotion();
  const [users, setUsers] = useState<Presence[]>([]);
  const wsRef  = useRef<WebSocket | null>(null);

  useEffect(() => {
    // Use the existing WS collab channel
    const wsUrl = `${window.location.origin.replace('http', 'ws')}/ws/collab/${orgId}`;
    let stopped = false;
    let delay   = 1000;

    function connect() {
      if (stopped) return;
      try {
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data) as { type: string; userId: string; name?: string; section?: string };
            if (msg.type === 'presence.update') {
              setUsers(prev => {
                const exists = prev.find(u => u.userId === msg.userId);
                if (exists) {
                  return prev.map(u => u.userId === msg.userId
                    ? { ...u, section: msg.section }
                    : u
                  );
                }
                return [...prev, {
                  userId:  msg.userId,
                  name:    msg.name ?? msg.userId.slice(0, 6),
                  color:   hashColor(msg.userId),
                  section: msg.section,
                }];
              });
            }
            if (msg.type === 'presence.leave') {
              setUsers(prev => prev.filter(u => u.userId !== msg.userId));
            }
          } catch {}
        };

        ws.onopen = () => { delay = 1000; };
        ws.onclose = () => {
          if (!stopped) setTimeout(() => { delay = Math.min(delay * 2, 30_000); connect(); }, delay);
        };
      } catch {}
    }

    connect();
    return () => { stopped = true; wsRef.current?.close(1000); setUsers([]); };
  }, [orgId]);

  if (users.length === 0) return null;

  const visible = users.slice(0, 5);
  const overflow = users.length - 5;

  return (
    <div
      className={cn('flex items-center gap-1.5', className)}
      role="status"
      aria-label={`${users.length} ${users.length === 1 ? 'person' : 'people'} viewing`}
    >
      {/* Avatar stack */}
      <div className="flex -space-x-2">
        <AnimatePresence mode="popLayout">
          {visible.map((user, i) => (
            <motion.div
              key={user.userId}
              layout
              initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.6, x: -8 }}
              animate={{ opacity: 1, scale: 1, x: 0 }}
              exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.5 }}
              transition={{ ...AVATAR_SPRING, delay: reduce ? 0 : i * 0.06 }}
              className="relative"
              style={{ zIndex: visible.length - i }}
            >
              {/* Avatar */}
              <div
                className="h-6 w-6 rounded-full border-2 border-[#0F1117] flex items-center justify-center text-[9px] font-bold text-white uppercase select-none"
                style={{ backgroundColor: user.color, boxShadow: `0 0 8px ${user.color}50` }}
                title={`${user.name}${user.section ? ` · ${user.section}` : ''}`}
                aria-label={user.name}
              >
                {user.name.slice(0, 2)}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>

        {overflow > 0 && (
          <div
            className="h-6 w-6 rounded-full border-2 border-[#0F1117] bg-[#252B3B] flex items-center justify-center text-[9px] font-bold text-[#94A3B8]"
            aria-label={`${overflow} more people`}
          >
            +{overflow}
          </div>
        )}
      </div>

      {/* Count label */}
      <span className="text-[11px] text-[#475569]">
        {users.length === 1 ? '1 viewing' : `${users.length} viewing`}
      </span>
    </div>
  );
}
