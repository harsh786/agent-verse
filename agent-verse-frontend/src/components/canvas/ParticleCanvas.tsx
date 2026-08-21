/**
 * ParticleCanvas — Canvas-based particle system for JARVIS neural visualization.
 * Spec §2.3: Renders all data-movement particles at 60fps.
 * Ring buffer of 500 active particles. Trail rendering. Glow filters.
 */
import { forwardRef, useEffect, useImperativeHandle, useRef, useCallback } from 'react';
import { useReducedMotion } from 'framer-motion';

export type ParticleShape = 'circle' | 'spark' | 'diamond' | 'star' | 'x';

export interface Particle {
  id:          string;
  from:        { x: number; y: number };
  to:          { x: number; y: number };
  color:       string;
  size:        number;
  trail:       boolean;
  trailLength: number;
  duration:    number;
  shape:       ParticleShape;
  onComplete?: () => void;
}

interface ActiveParticle extends Particle {
  startTime:   number;
  trail_points: Array<{ x: number; y: number; alpha: number }>;
}

export interface ParticleCanvasRef {
  emitParticle: (p: Particle) => void;
  emitBurst:    (center: { x: number; y: number }, color: string, count?: number) => void;
  clearAll:     () => void;
}

interface ParticleCanvasProps {
  width:     number;
  height:    number;
  className?: string;
}

const MAX_PARTICLES = 500;

function lerp(a: number, b: number, t: number) { return a + (b - a) * t; }

function easeInOut(t: number) { return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t; }

function drawShape(
  ctx: CanvasRenderingContext2D,
  shape: ParticleShape,
  x: number, y: number,
  size: number,
  color: string,
  alpha: number,
) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle   = color;
  ctx.strokeStyle = color;
  ctx.shadowColor = color;
  ctx.shadowBlur  = size * 2;

  switch (shape) {
    case 'circle':
      ctx.beginPath();
      ctx.arc(x, y, size, 0, Math.PI * 2);
      ctx.fill();
      break;
    case 'spark': {
      ctx.lineWidth = 1.5;
      const arms = 4;
      for (let i = 0; i < arms; i++) {
        const angle = (i / arms) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x + Math.cos(angle) * size * 2, y + Math.sin(angle) * size * 2);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.arc(x, y, size * 0.6, 0, Math.PI * 2);
      ctx.fill();
      break;
    }
    case 'diamond':
      ctx.beginPath();
      ctx.moveTo(x, y - size);
      ctx.lineTo(x + size, y);
      ctx.lineTo(x, y + size);
      ctx.lineTo(x - size, y);
      ctx.closePath();
      ctx.fill();
      break;
    case 'star':
      ctx.beginPath();
      for (let i = 0; i < 5; i++) {
        const outerAngle = (i * 4 * Math.PI) / 5 - Math.PI / 2;
        const innerAngle = ((i * 4 + 2) * Math.PI) / 5 - Math.PI / 2;
        if (i === 0) ctx.moveTo(x + Math.cos(outerAngle) * size, y + Math.sin(outerAngle) * size);
        else ctx.lineTo(x + Math.cos(outerAngle) * size, y + Math.sin(outerAngle) * size);
        ctx.lineTo(x + Math.cos(innerAngle) * size * 0.4, y + Math.sin(innerAngle) * size * 0.4);
      }
      ctx.closePath();
      ctx.fill();
      break;
    case 'x':
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x - size, y - size); ctx.lineTo(x + size, y + size);
      ctx.moveTo(x + size, y - size); ctx.lineTo(x - size, y + size);
      ctx.stroke();
      break;
  }
  ctx.restore();
}

export const ParticleCanvas = forwardRef<ParticleCanvasRef, ParticleCanvasProps>(
  ({ width, height, className = '' }, ref) => {
    const canvasRef     = useRef<HTMLCanvasElement>(null);
    const particles     = useRef<ActiveParticle[]>([]);
    const rafRef        = useRef<number | null>(null);
    const reduce        = useReducedMotion();

    const render = useCallback(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const now    = performance.now();
      const alive: ActiveParticle[] = [];

      for (const p of particles.current) {
        const elapsed = now - p.startTime;
        const rawT    = Math.min(elapsed / p.duration, 1);
        const t       = easeInOut(rawT);
        const cx      = lerp(p.from.x, p.to.x, t);
        const cy      = lerp(p.from.y, p.to.y, t);
        const alpha   = rawT < 0.8 ? 1 : (1 - rawT) / 0.2;

        // Draw trail
        if (p.trail && p.trail_points.length > 0) {
          for (let i = 0; i < p.trail_points.length; i++) {
            const tp  = p.trail_points[i];
            const ta  = tp.alpha * alpha * 0.4 * (i / p.trail_points.length);
            ctx.save();
            ctx.globalAlpha = ta;
            ctx.fillStyle   = p.color;
            ctx.shadowColor = p.color;
            ctx.shadowBlur  = p.size;
            ctx.beginPath();
            ctx.arc(tp.x, tp.y, p.size * 0.5, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
          }
          // Add current position to trail
          p.trail_points.push({ x: cx, y: cy, alpha });
          if (p.trail_points.length > 12) p.trail_points.shift();
          // Fade old trail points
          for (const tp of p.trail_points) tp.alpha *= 0.85;
        }

        drawShape(ctx, p.shape, cx, cy, p.size, p.color, alpha);

        if (rawT < 1) {
          alive.push(p);
        } else {
          p.onComplete?.();
        }
      }
      particles.current = alive;
      rafRef.current = requestAnimationFrame(render);
    }, []);

    useEffect(() => {
      if (reduce) return;
      rafRef.current = requestAnimationFrame(render);
      return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
    }, [render, reduce]);

    useImperativeHandle(ref, () => ({
      emitParticle(p: Particle) {
        if (reduce) return;
        if (particles.current.length >= MAX_PARTICLES) {
          particles.current.shift(); // evict oldest
        }
        particles.current.push({
          ...p,
          startTime:    performance.now(),
          trail_points: [],
        });
      },
      emitBurst(center, color, count = 8) {
        if (reduce) return;
        for (let i = 0; i < count; i++) {
          const angle  = (i / count) * Math.PI * 2;
          const dist   = 40 + Math.random() * 30;
          particles.current.push({
            id:          `burst-${Date.now()}-${i}`,
            from:        center,
            to:          { x: center.x + Math.cos(angle) * dist, y: center.y + Math.sin(angle) * dist },
            color,
            size:        3,
            trail:       false,
            trailLength: 0,
            duration:    400 + Math.random() * 200,
            shape:       'spark',
            startTime:   performance.now(),
            trail_points:[],
          });
        }
      },
      clearAll() { particles.current = []; },
    }), [reduce]);

    return (
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className={`pointer-events-none absolute inset-0 ${className}`}
        aria-hidden="true"
      />
    );
  }
);
ParticleCanvas.displayName = 'ParticleCanvas';
