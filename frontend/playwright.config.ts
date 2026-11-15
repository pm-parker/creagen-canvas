import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  use: {
    baseURL: "http://localhost:4173",
    headless: true,
  },
  webServer: [
    {
      command: "pnpm exec vite preview --port 4173 --strictPort",
      port: 4173,
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
});
