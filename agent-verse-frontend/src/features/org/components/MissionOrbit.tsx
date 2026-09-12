/**
 * MissionOrbit — JARVIS-style glowing mission orbit for the org sidebar.
 *
 * Shows active missions as glowing nodes orbiting a cyan hub node.
 * Each node pulses and shows mission title. Spring-animated entrance.
 * Designed for the w-72 sidebar (max 260px wide).
 *
 * Unlike AgentConstellation (600×480 d3-force), this is compact and always visible.
 */
import { motion } from 'framer-motion';
import { useReducedMotion } from 'framer-motion';
import type { OrgMission } from '../types';

interface MissionOrbitProps {
  missions: OrgMission[];
  className?: string;
  /** Canvas size in px (square). Defaults to the original compact 240. */
  size?: number;
}

const PALETTE = [
  '#00D4FF', // cyan
  '#6366F1', // indigo
  '#A855F7', // violet
  '#10B981', // emerald
  '#F59E0B', // amber
  '#EF4444', // red
];

const BASE = 240;   // reference canvas size the layout was tuned for

function getOrbitPosition(index: number, total: number, radius: number, cx: number, cy: number) {
  const angle = (index / total) * 2 * Math.PI - Math.PI / 2;
  return {
    x: cx + radius * Math.cos(angle),
    y: cy + radius * Math.sin(angle),
  };
}

export function MissionOrbit({ missions, className, size = BASE }: MissionOrbitProps) {
  const reduce  = useReducedMotion();
  const active  = missions.filter(m => m.status === 'active').slice(0, 8);
  const total   = active.length;
  // Everything scales off the requested size so the orbit can fill a larger hero.
  const k       = size / BASE;
  const CX      = size / 2;
  const CY      = size / 2;
  const radius  = (total <= 3 ? 68 : total <= 6 ? 80 : 90) * k;
  const hubSize = 40 * k;
  const nodeSize = 52 * k;

  if (total === 0) return null;

  return (
    <div
      className={`relative select-none ${className ?? ''}`}
      style={{ width: size, height: size }}
      aria-label={`${total} active mission${total !== 1 ? 's' : ''} orbiting`}
    >
      {/* Background subtle glow */}
      <div
        className="absolute inset-0 rounded-full"
        style={{
          background: 'radial-gradient(circle at center, rgba(0,212,255,0.04) 0%, transparent 70%)',
        }}
        aria-hidden
      />

      {/* Orbit ring */}
      <svg
        className="absolute inset-0 pointer-events-none"
        width={size}
        height={size}
        aria-hidden
      >
        {/* Outer orbit ring */}
        <circle
          cx={CX} cy={CY} r={radius}
          fill="none"
          stroke="rgba(0,212,255,0.08)"
          strokeWidth={1}
          strokeDasharray="4 4"
        />
        {/* Lines from hub to each mission */}
        {active.map((_, i) => {
          const pos = getOrbitPosition(i, total, radius, CX, CY);
          return (
            <line
              key={i}
              x1={CX} y1={CY}
              x2={pos.x} y2={pos.y}
              stroke="rgba(0,212,255,0.06)"
              strokeWidth={1}
            />
          );
        })}
      </svg>

      {/* Center hub — organization node */}
      <motion.div
        className="absolute flex items-center justify-center rounded-full border-2 border-[#00D4FF]/50 bg-[#050A14]"
        style={{
          width: hubSize, height: hubSize,
          left: CX - hubSize / 2, top: CY - hubSize / 2,
          boxShadow: '0 0 16px rgba(0,212,255,0.45), 0 0 40px rgba(0,212,255,0.12)',
        }}
        animate={reduce ? {} : { scale: [1, 1.06, 1], opacity: [0.9, 1, 0.9] }}
        transition={{ duration: 2.8, repeat: Infinity, ease: 'easeInOut' }}
        aria-label="Organization hub"
      >
        <span className="text-[#00D4FF] text-sm font-bold">⚡</span>
      </motion.div>

      {/* Mission nodes */}
      {active.map((mission, i) => {
        const pos   = getOrbitPosition(i, total, radius, CX, CY);
        const color = PALETTE[i % PALETTE.length];
        const delay = i * 0.12;

        return (
          <motion.div
            key={mission.id}
            className="absolute"
            style={{ left: pos.x - nodeSize / 2, top: pos.y - nodeSize / 2 }}
            initial={{ opacity: 0, scale: 0 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ type: 'spring', stiffness: 320, damping: 24, delay }}
          >
            {/* Glow ring */}
            {!reduce && (
              <motion.div
                className="absolute inset-0 rounded-full"
                style={{ border: `1px solid ${color}` }}
                animate={{ scale: [1, 1.6, 1], opacity: [0.4, 0, 0.4] }}
                transition={{ duration: 2 + i * 0.3, repeat: Infinity, delay }}
              />
            )}

            {/* Node */}
            <div
              className="relative rounded-full flex flex-col items-center justify-center cursor-pointer"
              style={{
                width: nodeSize, height: nodeSize,
                background: `${color}14`,
                border: `1.5px solid ${color}40`,
                boxShadow: `0 0 10px ${color}30`,
              }}
              title={mission.title}
              aria-label={`Mission: ${mission.title}`}
            >
              {/* Status dot */}
              <motion.div
                className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full"
                style={{ background: color, boxShadow: `0 0 6px ${color}` }}
                animate={reduce ? {} : { opacity: [1, 0.4, 1] }}
                transition={{ duration: 1.4, repeat: Infinity, delay }}
              />
              {/* Mission title (truncated to 2 chars initials) */}
              <span
                className="text-[10px] font-bold text-center leading-tight px-1 truncate w-full text-center"
                style={{ color }}
              >
                {mission.title.slice(0, 8)}
              </span>
              <span className="text-[8px] opacity-50" style={{ color }}>
                {mission.priority?.toUpperCase().slice(0, 3)}
              </span>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}
