import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { assertPublicSupabaseKey } from "./src/public-config.ts";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  assertPublicSupabaseKey(env.VITE_SUPABASE_PUBLISHABLE_KEY ?? "");
  return {
  plugins: [react()],
  server: { port: 5173 },
  };
});

