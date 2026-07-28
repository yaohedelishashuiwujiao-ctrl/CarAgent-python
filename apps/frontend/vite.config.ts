import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  cacheDir: "/tmp/subjects-agent-vite",
  server: {
    proxy: {
      "/api": process.env.PLATFORM_API_BASE_URL || "http://127.0.0.1:8000",
    },
  },
});
