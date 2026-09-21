import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the app is served from :5173 and the API from :8000, so /api
// is proxied rather than hit cross-origin -- which keeps the frontend's fetch
// paths identical in dev and in a built checkout, where FastAPI serves both.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
