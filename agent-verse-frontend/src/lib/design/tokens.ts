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
