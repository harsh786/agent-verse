/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // JARVIS is always dark -- but class strategy allows override
  darkMode: "class",
  theme: {
    extend: {
      // -- Font families (frontend-design: Inter + JetBrains Mono) ---------
      fontFamily: {
        sans:    ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono:    ['JetBrains Mono', 'Fira Code', 'monospace'],
        display: ['Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        // impeccable-ui: body >= 15px, scale from there
        xs:  ['11px', { lineHeight: '1.5' }],
        sm:  ['13px', { lineHeight: '1.5' }],
        base:['15px', { lineHeight: '1.6' }],
        lg:  ['17px', { lineHeight: '1.55' }],
        xl:  ['19px', { lineHeight: '1.4'  }],
        '2xl':['22px', { lineHeight: '1.3', letterSpacing: '-0.01em' }],
        '3xl':['28px', { lineHeight: '1.2', letterSpacing: '-0.02em' }],
        '4xl':['36px', { lineHeight: '1.1', letterSpacing: '-0.03em' }],
      },
      colors: {
        // -- shadcn-compatible tokens (bound to CSS vars) -----------------
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: "hsl(var(--card))",
        "card-foreground": "hsl(var(--card-foreground))",
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        primary: "hsl(var(--primary))",
        "primary-foreground": "hsl(var(--primary-foreground))",
        secondary: "hsl(var(--secondary))",
        "secondary-foreground": "hsl(var(--secondary-foreground))",
        muted: "hsl(var(--muted))",
        "muted-foreground": "hsl(var(--muted-foreground))",
        accent: "hsl(var(--accent))",
        "accent-foreground": "hsl(var(--accent-foreground))",
        destructive: "hsl(var(--destructive))",
        "destructive-foreground": "hsl(var(--destructive-foreground))",
        ring: "hsl(var(--ring))",

        // -- JARVIS direct palette (use these in new code) -----------------
        jarvis: {
          base:     "#0A0D14",  // page background
          primary:  "#0F1117",  // default surface
          secondary:"#1A1F2E",  // cards, panels
          elevated: "#252B3B",  // hover, selected
          overlay:  "#2D3748",  // modals, dropdowns
        },
        // -- Mission Control (legacy -- kept for existing components) -------
        "command-black":  "#080A12",
        "panel-graphite": "#111827",
        "neural-violet":  "#7C3AED",
        "telemetry-cyan": "#06B6D4",
        "verified-green": "#22C55E",
        "risk-amber":     "#F59E0B",
        "mission-red":    "#EF4444",
      },
      borderRadius: {
        lg:   "var(--radius)",
        md:   "calc(var(--radius) - 2px)",
        sm:   "calc(var(--radius) - 4px)",
        xl:   "calc(var(--radius) + 4px)",
        "2xl":"calc(var(--radius) + 8px)",
      },
      // -- Box shadows (JARVIS glow system) ------------------------------
      boxShadow: {
        "glow-blue":  "0 0 20px rgba(0,212,255,0.15)",
        "glow-electric": "0 0 20px rgba(0,212,255,0.15)",
        "glow-electric-strong": "0 0 40px rgba(0,212,255,0.35)",
        "glow-cyan":  "0 0 15px rgba(6,182,212,0.2)",
        "glow-violet":"0 0 20px rgba(139,92,246,0.2)",
        "card":       "0 4px 24px rgba(0,0,0,0.4)",
        "modal":      "0 20px 60px rgba(0,0,0,0.6)",
      },
      // -- Animation (motion tokens for spring components) ---------------
      keyframes: {
        "fade-in":    { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-in-up": { from: { opacity: "0", transform: "translateY(16px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "slide-in":   { from: { transform: "translateX(-8px)", opacity: "0" }, to: { transform: "translateX(0)", opacity: "1" } },
        "pulse-glow": { "0%,100%": { opacity: "0.8" }, "50%": { opacity: "0.3" } },
        "shimmer":    { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
        "blink":      { "0%,100%": { opacity: "1" }, "50%": { opacity: "0" } },
        "orb-drift":  {
          "0%,100%": { transform: "translate(0,0) scale(1)" },
          "33%":     { transform: "translate(30px,-20px) scale(1.05)" },
          "66%":     { transform: "translate(-20px,15px) scale(0.97)" },
        },
        marquee: { from: { transform: "translateX(0)" }, to: { transform: "translateX(-50%)" } },
      },
      animation: {
        "fade-in":     "fade-in 0.3s ease both",
        "fade-in-up":  "fade-in-up 0.4s cubic-bezier(0.22,1,0.36,1) both",
        "slide-in":    "slide-in 0.25s ease both",
        "pulse-glow":  "pulse-glow 2s ease-in-out infinite",
        "shimmer":     "shimmer 2s linear infinite",
        "blink":       "blink 1s step-end infinite",
        "orb-drift":   "orb-drift 14s ease-in-out infinite",
        "marquee":     "marquee 28s linear infinite",
      },
      // -- Backdrop blur (glass cards) -----------------------------------
      backdropBlur: { xs: "4px" },
    },
  },
  plugins: [],
};
