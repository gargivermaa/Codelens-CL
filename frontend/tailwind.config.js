/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0b1020",
        surface: "#131a2a",
        raised: "#1a2235",
        line: "#26304a",
        ink: "#e6e9f2",
        muted: "#8f98ad",
        faint: "#5f6880",
        accent: "#8b7cf6",
        accent2: "#3987e5",
        good: "#0ca30c",
        warning: "#fab219",
        serious: "#ec835a",
        critical: "#d03b3b",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
