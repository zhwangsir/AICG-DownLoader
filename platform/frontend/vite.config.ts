import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3501,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8100",
        changeOrigin: true,
      },
      "/static": {
        target: "http://127.0.0.1:8100",
        changeOrigin: true,
      },
    },
  },
});
