import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.OMEGA_API_PROXY_TARGET ?? "http://127.0.0.1:8770";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": apiProxyTarget
    }
  }
});
