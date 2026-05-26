import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    container: {
      center: true,
      padding: "1rem",
      // Allow `.container` to grow on very wide displays so 4K monitors
      // (3840px) don't leave ~1200px of dead whitespace on each side.
      // The Tailwind `container` utility caps width at the screen breakpoint
      // matching the current viewport — adding 3xl/4xl steps lets the master
      // architecture diagram and timeline use that extra horizontal space.
      screens: {
        "2xl": "1400px",
        "3xl": "1800px",
        "4xl": "2400px",
      },
    },
    extend: {
      // Custom breakpoints so `.container` and utility classes can target
      // ultra-wide / 4K displays. Without these, `.container` is hard-capped
      // at 1400px (2xl) and ~1200px of whitespace shows on each side at 3840px.
      screens: {
        "3xl": "1800px",
        "4xl": "2400px",
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        neon: {
          cyan: "#00f0ff",
          magenta: "#ff00d4",
          lime: "#9aff00",
          amber: "#ffb800",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      keyframes: {
        "pulse-glow": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(0,240,255,0.4)" },
          "50%": { boxShadow: "0 0 24px 4px rgba(0,240,255,0.6)" },
        },
        "ticker-tape": {
          "0%": { transform: "translateX(100%)" },
          "100%": { transform: "translateX(-100%)" },
        },
      },
      animation: {
        "pulse-glow": "pulse-glow 2s ease-in-out infinite",
        "ticker-tape": "ticker-tape 20s linear infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
