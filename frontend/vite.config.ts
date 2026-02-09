import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const usePolling = env.CHOKIDAR_USEPOLLING === "1";
  const intervalRaw = Number(env.CHOKIDAR_INTERVAL ?? "1000");
  const interval = Number.isFinite(intervalRaw) && intervalRaw > 0 ? intervalRaw : 1000;

  return {
    plugins: [react()],
    server: {
      port: 5173,
      host: true,
      watch: {
        usePolling,
        interval
      }
    }
  };
});
