/**
 * AgentVerse Design Tokens — Single Source of Truth
 * All Jarvis design values live here. Import instead of hardcoding.
 * Synced with globals.css CSS variables.
 */

export const colors = {
  // Primary electric — the Jarvis signature
  electric:        '#00D4FF',
  electricDim:     'rgba(0,212,255,0.15)',
  electricBright:  'rgba(0,212,255,0.60)',
  electricGlow:    'rgba(0,212,255,0.30)',

  // Semantic
  emerald:    '#10B981',  emeraldDim: 'rgba(16,185,129,0.15)',
  amber:      '#F59E0B',  amberDim:   'rgba(245,158,11,0.15)',
  rose:       '#EF4444',  roseDim:    'rgba(239,68,68,0.15)',
  violet:     '#8B5CF6',  violetDim:  'rgba(139,92,246,0.15)',

  // Surface hierarchy (dark → darkest)
  surface0: '#020408',
  surface1: '#0A0D14',
  surface2: '#0F1117',
  surface3: '#1A1F2E',
  surface4: '#252B3B',
  surface5: '#2D3748',

  // Text
  text1:        '#F1F5F9',
  text2:        '#94A3B8',
  text3:        '#475569',
  textElectric: '#00D4FF',
} as const;

export const shadows = {
  electric:       '0 0 20px rgba(0,212,255,0.15)',
  electricStrong: '0 0 40px rgba(0,212,255,0.35)',
  card:           '0 4px 24px rgba(0,0,0,0.4)',
  modal:          '0 20px 60px rgba(0,0,0,0.6)',
} as const;

/** Tailwind class helpers — use in className instead of arbitrary values */
export const tw = {
  card:          'bg-[#1A1F2E] border border-white/[0.07] rounded-xl',
  cardHover:     'hover:border-[#00D4FF]/25 hover:shadow-glow-electric hover:-translate-y-0.5 transition-[border-color,box-shadow,transform]',
  glass:         'bg-white/[0.05] backdrop-blur-xl border border-white/[0.08] rounded-xl',
  electricText:  'text-[#00D4FF]',
  electricBg:    'bg-[rgba(0,212,255,0.12)]',
  electricBorder:'border-[rgba(0,212,255,0.60)]',
  electricRing:  'focus:ring-[#00D4FF]/60 focus:border-[#00D4FF]',
  surface1:      'bg-[#0A0D14]',
  surface2:      'bg-[#0F1117]',
  surface3:      'bg-[#1A1F2E]',
} as const;

// JARVIS spec-compatible alias (spec §2.1)
export const tokens = {
  color: {
    electric: colors.electric, electricDim: colors.electricDim,
    electricGlow: colors.electricGlow, electricBright: colors.electricBright,
    emerald: '#00E676', emeraldDim: 'rgba(0,230,118,0.15)',
    rose: '#FF3366', roseDim: 'rgba(255,51,102,0.15)',
    amber: '#FFB300', amberDim: 'rgba(255,179,0,0.15)',
    indigo: '#6366F1', indigoDim: 'rgba(99,102,241,0.15)',
    violet: '#A855F7', violetDim: 'rgba(168,85,247,0.15)',
    surface0: colors.surface0, surface1: colors.surface1,
    surface2: colors.surface2, surface3: colors.surface3,
    surface4: colors.surface4, surface5: colors.surface5,
    text1: colors.text1, text2: colors.text2, text3: colors.text3,
    border1: 'rgba(255,255,255,0.06)',
    border2: 'rgba(255,255,255,0.10)',
    border3: 'rgba(255,255,255,0.16)',
  },
  glow: {
    tier1: '0 0 8px rgba(0,212,255,0.12)',
    tier2: '0 0 16px rgba(0,212,255,0.30), 0 0 4px rgba(0,212,255,0.60)',
    tier3: '0 0 32px rgba(0,212,255,0.50), 0 0 8px rgba(0,212,255,0.80)',
    emerald: '0 0 16px rgba(0,230,118,0.30)',
    rose: '0 0 16px rgba(255,51,102,0.30)',
    amber: '0 0 16px rgba(255,179,0,0.30)',
    indigo: '0 0 16px rgba(99,102,241,0.30)',
    violet: '0 0 16px rgba(168,85,247,0.30)',
  },
  glass: {
    panel: 'backdrop-blur-md bg-[#0A0F1A]/80 border border-white/[0.06]',
    card: 'backdrop-blur-sm bg-[#0F1826]/90 border border-white/[0.08]',
    elevated: 'backdrop-blur-md bg-[#162035]/90 border border-white/10',
    overlay: 'backdrop-blur-xl bg-[#020408]/60',
  },
} as const;
