/** Liquid Intelligent Technologies brand palette. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          navy: "#273c87",
          "navy-dark": "#1d2d66",
          "navy-tint": "#e9ecf5",
          magenta: "#c8187d",
          "magenta-tint": "#fbe8f3",
          surface: "#f1f1f1",
        },
      },
      fontFamily: {
        sans: ["Montserrat", "Inter", "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(39,60,135,0.06), 0 4px 12px rgba(39,60,135,0.06)",
      },
    },
  },
  plugins: [],
};
