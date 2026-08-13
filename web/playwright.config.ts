import { defineConfig } from "@playwright/test";

// The era-coverage pass. It renders the site against whatever database
// DATABASE_URL points at and asserts that the rating surfaces hold rows, per
// era — a check a status code cannot make, because an empty page returns 200.
//
// It reuses the dev server if one is already up, and starts its own only when
// there is none. Playwright stops only servers it started, so the dev server a
// working session leaves running on 3000 survives the pass — and this version
// of Next refuses a second dev server from the same directory anyway.
const PORT = Number(process.env.E2E_PORT ?? 3000);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "list" : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npx next dev --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: true,
    timeout: 120_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
