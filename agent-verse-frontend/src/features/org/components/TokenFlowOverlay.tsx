/**
 * TokenFlowOverlay — Token particles flowing over active step nodes.
 * Spec §3.6: Visualizes token stream density proportional to TPS.
 */
import { useEffect, useRef } from 'react';
import { useReducedMotion } from 'framer-motion';

interface TokenFlowOverlayProps {
  isActive:  boolean;
  tps?:      number;   // tokens per second
  width:     number;
  height:    number;
  fromX:     number;
  fromY:     number;
  toX:       number;
  toY:       number;
  className?: string;
}

interface FlowParticle {
  x: number; y: number; vx: number; vy: number;
  alpha: number; life: number; maxLife: number;
}

export function TokenFlowOverlay({ isActive, tps = 20, width, height, fromX, fromY, toX, toY, className = '' }: TokenFlowOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef   = useRef<number | null>(null);
  const particles = useRef<FlowParticle[]>([]);
  const reduce    = useReducedMotion();

  useEffect(() => {
    if (reduce || !isActive) {
      if (animRef.current) cancelAnimationFrame(animRef.current);
      particles.current = [];
      return;
    }

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const spawnInterval = Math.max(20, 1000 / Math.min(tps, 50));
    let lastSpawn = 0;

    function frame(now: number) {
      ctx!.clearRect(0, 0, canvas!.width, canvas!.height);

      // Spawn particles along the flow direction
      if (now - lastSpawn > spawnInterval) {
        const t    = Math.random();
        const x    = fromX + (toX - fromX) * t * 0.3;
        const y    = fromY + (toY - fromY) * t * 0.3;
        const dx   = (toX - fromX) * 0.003;
        const dy   = (toY - fromY) * 0.003;
        particles.current.push({ x, y, vx: dx + (Math.random() - 0.5) * 0.5, vy: dy + (Math.random() - 0.5) * 0.5, alpha: 0.8, life: 0, maxLife: 60 + Math.random() * 40 });
        if (particles.current.length > 80) particles.current.shift();
        lastSpawn = now;
      }

      const alive: FlowParticle[] = [];
      for (const p of particles.current) {
        p.x    += p.vx;
        p.y    += p.vy;
        p.life += 1;
        p.alpha = 0.8 * (1 - p.life / p.maxLife);
        if (p.life < p.maxLife) alive.push(p);

        ctx!.save();
        ctx!.globalAlpha = p.alpha;
        ctx!.fillStyle   = '#00D4FF';
        ctx!.shadowColor = '#00D4FF';
        ctx!.shadowBlur  = 4;
        ctx!.beginPath();
        ctx!.arc(p.x, p.y, 1.5, 0, Math.PI * 2);
        ctx!.fill();
        ctx!.restore();
      }
      particles.current = alive;
      animRef.current = requestAnimationFrame(frame);
    }

    animRef.current = requestAnimationFrame(frame);
    return () => { if (animRef.current) cancelAnimationFrame(animRef.current); };
  }, [isActive, tps, fromX, fromY, toX, toY, reduce]);

  if (reduce) return null;

  return (
    <canvas
      ref={canvasRef}
      width={width}
      height={height}
      className={`pointer-events-none absolute inset-0 ${className}`}
      aria-hidden
    />
  );
}
