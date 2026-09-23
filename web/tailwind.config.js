/**
 * IVaaS design tokens. Colours are CSS variables so light and dark themes are one set of
 * class names; see src/index.css for the values. Brand: Liquid Intelligent Technologies.
 */
export default {
  darkMode: ["class", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ground: "rgb(var(--ground) / <alpha-value>)",
        surface: "rgb(var(--surface) / <alpha-value>)",
        raised: "rgb(var(--raised) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        faint: "rgb(var(--faint) / <alpha-value>)",
        brand: {
          DEFAULT: "rgb(var(--brand) / <alpha-value>)",
          deep: "rgb(var(--brand-deep) / <alpha-value>)",
          tint: "rgb(var(--brand-tint) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          tint: "rgb(var(--accent-tint) / <alpha-value>)",
        },
        good: "rgb(var(--good) / <alpha-value>)",
        warn: "rgb(var(--warn) / <alpha-value>)",
        bad: "rgb(var(--bad) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["Manrope", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 0 rgb(var(--ink) / 0.04), 0 8px 24px -12px rgb(var(--ink) / 0.18)",
        lift: "0 12px 32px -12px rgb(var(--ink) / 0.28)",
      },
      borderRadius: { lg: "10px", xl: "14px", "2xl": "18px" },
      transitionTimingFunction: { out: "cubic-bezier(0.2, 0.7, 0.2, 1)" },
    },
  },
  plugins: [],
};
