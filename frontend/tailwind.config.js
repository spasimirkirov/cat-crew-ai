/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // CatCrewAI palette (frontend brief)
        bg: "#0B0D12",
        card: "#13161F",
        line: "#222634",
        cyan: { DEFAULT: "#38BDF8" },
        emerald: { DEFAULT: "#34D399" },
        violet: { DEFAULT: "#A78BFA" },
        muted: "#8B92A8",
        ink: "#E6E8F0",
        danger: "#F87171",
        warn: "#FBBF24",
      },
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", "-apple-system", "BlinkMacSystemFont", '"SF Pro Text"', '"Segoe UI"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: { xl: "0.9rem", "2xl": "1.15rem" },
      boxShadow: {
        glow: "0 0 0 1px rgba(56,189,248,.55), 0 0 24px rgba(56,189,248,.28)",
        card: "0 1px 0 rgba(255,255,255,.02) inset, 0 8px 24px rgba(0,0,0,.28)",
      },
      keyframes: {
        // a cat joins the fleet: slide + fade in, with a short cyan halo
        "pop-in": {
          "0%": { opacity: "0", transform: "translateX(-14px) scale(.96)", boxShadow: "0 0 0 1px rgba(56,189,248,.9), 0 0 28px rgba(56,189,248,.55)" },
          "60%": { opacity: "1", transform: "translateX(0) scale(1.01)" },
          "100%": { opacity: "1", transform: "translateX(0) scale(1)", boxShadow: "0 0 0 1px rgba(56,189,248,0), 0 0 0 rgba(56,189,248,0)" },
        },
        "fade-up": { "0%": { opacity: "0", transform: "translateY(6px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        "dot-pulse": { "0%,100%": { opacity: ".35", transform: "scale(.85)" }, "50%": { opacity: "1", transform: "scale(1)" } },
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
      },
      animation: {
        "pop-in": "pop-in .7s cubic-bezier(.2,.8,.2,1) both",
        "fade-up": "fade-up .25s ease-out both",
        "dot-pulse": "dot-pulse 1.1s ease-in-out infinite",
        shimmer: "shimmer 2.2s linear infinite",
      },
    },
  },
  plugins: [],
};
