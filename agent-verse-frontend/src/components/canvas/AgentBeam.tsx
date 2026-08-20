/**
 * AgentBeam — SVG beam that animates along a path between two agent nodes.
 * Spec §3.4: Cubic-bezier path, glowing particle traveling the edge.
 */
import { useEffect, useRef } from 'react';
import { useReducedMotion } from 'framer-motion';

interface AgentBeamProps {
  from:     { x: number; y: number };
  to:       { x: number; y: number };
  color?:   string;
  duration?: number;  // ms
  width?:   number;
  height?:  number;
  className?: string;
}

export function AgentBeam({
  from, to, color = '#00D4FF', duration = 800, width = 600, height = 400, className = '',
}: AgentBeamProps) {
  const svgRef  = useRef<SVGSVGElement>(null);
  const reduce  = useReducedMotion();

  // Cubic bezier control points
  const midX = (from.x + to.x) / 2;
  const midY = (from.y + to.y) / 2 - 40;
  const d    = `M ${from.x} ${from.y} Q ${midX} ${midY} ${to.x} ${to.y}`;

  useEffect(() => {
    if (reduce) return;
    const svg = svgRef.current;
    if (!svg) return;
    // Remove existing animation circles
    svg.querySelectorAll('.beam-dot').forEach(el => el.remove());

    const path = svg.querySelector('path.beam-path') as SVGPathElement | null;
    if (!path) return;

    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('r', '4');
    circle.setAttribute('fill', color);
    circle.setAttribute('class', 'beam-dot');
    circle.style.filter = `drop-shadow(0 0 4px ${color})`;

    const anim = document.createElementNS('http://www.w3.org/2000/svg', 'animateMotion');
    anim.setAttribute('dur', `${duration}ms`);
    anim.setAttribute('repeatCount', '1');
    anim.setAttribute('fill', 'freeze');

    const mpath = document.createElementNS('http://www.w3.org/2000/svg', 'mpath');
    mpath.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', '#beam-path-id');
    anim.appendChild(mpath);
    circle.appendChild(anim);
    svg.appendChild(circle);
    (anim as any).beginElement?.();
  }, [from.x, from.y, to.x, to.y, color, duration, reduce]);

  if (reduce) return null;

  return (
    <svg
      ref={svgRef}
      width={width}
      height={height}
      className={`pointer-events-none absolute inset-0 ${className}`}
      aria-hidden
    >
      <defs>
        <filter id="beam-glow">
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      <path
        id="beam-path-id"
        className="beam-path"
        d={d}
        stroke={color}
        strokeWidth={1.5}
        fill="none"
        strokeOpacity={0.25}
        filter="url(#beam-glow)"
      />
    </svg>
  );
}
