import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the dashboard runs on :5173 and proxies API calls to FastAPI on :8000.
// In production FastAPI serves the built files from frontend/dist.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
  build: {
    rollupOptions: {
      output: { manualChunks: { charts: ["recharts"], react: ["react", "react-dom", "react-router-dom"] } },
    },
  },
});
