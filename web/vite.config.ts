import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const apiTarget = process.env.NEXUS_API_TARGET ?? "http://127.0.0.1:8766";

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": apiTarget,
    },
  },
});
