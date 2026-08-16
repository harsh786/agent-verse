/**
 * Tests for Workflow Engine design tokens and motion system.
 */
import { describe, it, expect } from 'vitest';
import {
  NODE_COLORS, NODE_ICONS, NODE_LABELS, NODE_CATEGORIES, NODE_SHAPES,
  STATUS_COLORS, PRIORITY_COLORS, getNodeClasses, getStatusClasses,
} from '../design/tokens';
import { springs, nodeBounce, panelSlide, toastEnter } from '../design/motion';

describe('Design tokens', () => {
  it('has a color entry for all 14 node types', () => {
    const types = [
      'trigger', 'llm', 'rag', 'tool', 'http', 'conditional', 'parallel',
      'foreach', 'hitl', 'transform', 'set_variable', 'sub_workflow', 'wait',
      'code', 'emit_event',
    ];
    for (const t of types) {
      expect(NODE_COLORS[t as keyof typeof NODE_COLORS]).toBeDefined();
    }
  });

  it('has an icon for all 14 node types', () => {
    const types = Object.keys(NODE_COLORS);
    for (const t of types) {
      expect(NODE_ICONS[t]).toBeTruthy();
    }
  });

  it('has a label for all 14 node types', () => {
    const types = Object.keys(NODE_COLORS);
    for (const t of types) {
      expect(NODE_LABELS[t]).toBeTruthy();
    }
  });

  it('has shape for all node types', () => {
    for (const t of Object.keys(NODE_COLORS)) {
      expect(NODE_SHAPES[t]).toBeTruthy();
    }
  });

  it('NODE_CATEGORIES covers all non-trigger types', () => {
    const allInCategories = Object.values(NODE_CATEGORIES).flat();
    const nodeTypes = Object.keys(NODE_COLORS);
    for (const t of nodeTypes) {
      expect(allInCategories).toContain(t);
    }
  });

  it('STATUS_COLORS has all run status values', () => {
    const statuses = ['pending', 'running', 'waiting_hitl', 'paused', 'complete', 'failed', 'cancelled', 'timed_out'];
    for (const s of statuses) {
      expect(STATUS_COLORS[s as keyof typeof STATUS_COLORS]).toBeDefined();
    }
  });

  it('PRIORITY_COLORS has all 4 priority levels', () => {
    expect(PRIORITY_COLORS.critical).toBeDefined();
    expect(PRIORITY_COLORS.high).toBeDefined();
    expect(PRIORITY_COLORS.medium).toBeDefined();
    expect(PRIORITY_COLORS.low).toBeDefined();
  });

  it('getNodeClasses returns a non-empty string', () => {
    const cls = getNodeClasses('llm');
    expect(cls).toBeTruthy();
    expect(typeof cls).toBe('string');
  });

  it('getNodeClasses returns fallback for unknown type', () => {
    const cls = getNodeClasses('unknown_type_xyz');
    expect(cls).toBeTruthy();
  });

  it('getStatusClasses returns correct classes for complete', () => {
    const cls = getStatusClasses('complete');
    expect(cls).toContain('emerald');
  });

  it('getStatusClasses returns correct classes for failed', () => {
    const cls = getStatusClasses('failed');
    expect(cls).toContain('red');
  });
});

describe('Motion system', () => {
  it('springs have all 4 presets', () => {
    expect(springs.snappy).toBeDefined();
    expect(springs.bouncy).toBeDefined();
    expect(springs.gentle).toBeDefined();
    expect(springs.smooth).toBeDefined();
  });

  it('each spring preset is type: spring', () => {
    for (const [, spring] of Object.entries(springs)) {
      expect(spring.type).toBe('spring');
    }
  });

  it('nodeBounce has initial, animate, exit variants', () => {
    expect(nodeBounce.initial).toBeDefined();
    expect(nodeBounce.animate).toBeDefined();
    expect(nodeBounce.exit).toBeDefined();
  });

  it('panelSlide enters from right (x: 100%)', () => {
    expect((panelSlide.initial as Record<string, unknown>).x).toBe('100%');
    expect((panelSlide.animate as Record<string, unknown>).x).toBe(0);
  });

  it('toastEnter bounces in from below', () => {
    const init = toastEnter.initial as Record<string, unknown>;
    expect(typeof init.y).toBe('number');
    expect((init.y as number) > 0).toBe(true);
  });
});
