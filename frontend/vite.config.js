import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build lands in frontend/dist, which the gateway serves. Keeping the
// output inside the frontend folder means the whole browser lane is one
// directory - nothing of it leaks into src/.
//
// The dev proxy is what makes `npm run dev` usable: the page runs on :5173
// and the gateway on :7860, so without it every fetch is a cross-origin
// request and the browser blocks it. Proxying instead of enabling CORS keeps
// production single-origin, which is one fewer thing to configure on deploy.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:7860",
      "/session": "http://localhost:7860",
      "/interviews": "http://localhost:7860",
      "/health": "http://localhost:7860",
    },
  },
});
