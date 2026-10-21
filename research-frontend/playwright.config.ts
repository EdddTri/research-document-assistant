import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 7_000 },
  use: {
    baseURL: "http://localhost:15173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
  },
  webServer: [
    {
      command: "uv run --locked python ../research-frontend/e2e/server.py",
      cwd: "../research-backend",
      url: "http://127.0.0.1:18010/health",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "npm run dev -- --port 15173",
      url: "http://localhost:15173",
      env: { VITE_API_BASE_URL: "http://localhost:18010" },
      reuseExistingServer: false,
    },
  ],
});
