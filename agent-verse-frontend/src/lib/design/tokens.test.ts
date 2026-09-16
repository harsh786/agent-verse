/**
 * Tests for the design token single-source-of-truth. These assert the shape and
 * key values other modules rely on, so an accidental rename/removal is caught.
 */
import { describe, expect, test } from 'vitest';
import { colors, shadows, tw, tokens } from './tokens';

describe('design tokens', () => {
  test('colors expose the Jarvis electric signature and surface hierarchy', () => {
    expect(colors.electric).toBe('#00D4FF');
    // textElectric is an alias of the electric signature colour.
    expect(colors.textElectric).toBe(colors.electric);
    // Surfaces are ordered dark → darkest and every level is present.
    for (const key of ['surface0', 'surface1', 'surface2', 'surface3', 'surface4', 'surface5'] as const) {
      expect(colors[key]).toMatch(/^#[0-9A-Fa-f]{6}$/);
    }
  });

  test('every color value is a non-empty CSS color string', () => {
    for (const [key, value] of Object.entries(colors)) {
      expect(value, key).toBeTruthy();
      expect(typeof value).toBe('string');
      expect(value).toMatch(/^(#|rgba?\()/);
    }
  });

  test('shadows include the required elevation roles', () => {
    expect(Object.keys(shadows).sort()).toEqual(['card', 'electric', 'electricStrong', 'modal']);
    expect(shadows.electric).toContain('rgba(0,212,255');
  });

  test('tailwind helpers reference the electric colour class', () => {
    expect(tw.electricText).toContain('#00D4FF');
    // card helper composes background + border + radius utilities.
    expect(tw.card).toMatch(/rounded-xl/);
    expect(tw.card).toMatch(/border/);
  });

  test('tokens.color mirrors the shared surface + text scale from colors', () => {
    expect(tokens.color.surface0).toBe(colors.surface0);
    expect(tokens.color.text1).toBe(colors.text1);
    expect(tokens.color.electric).toBe(colors.electric);
    // Three border tiers are defined for the JARVIS spec.
    expect(tokens.color.border1).toBeDefined();
    expect(tokens.color.border2).toBeDefined();
    expect(tokens.color.border3).toBeDefined();
  });

  test('tokens.glow defines a three-tier electric glow plus semantic glows', () => {
    expect(Object.keys(tokens.glow)).toEqual(
      expect.arrayContaining(['tier1', 'tier2', 'tier3', 'emerald', 'rose', 'amber']),
    );
    // Each glow is a box-shadow string mentioning a pixel blur radius.
    for (const value of Object.values(tokens.glow)) {
      expect(value).toMatch(/px/);
    }
  });
});
