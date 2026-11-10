import { defineConfig, devices } from "@playwright/test"

// End-to-end tests run against a live API server (which serves the built app):
//   npm run build && (cd .. && python -m uvicorn api.app:create_app --factory --port 8000)
//   npm run e2e
// `npm run screenshots` re-captures the README images into docs/img; `npm run demo`
// records the frames for docs/img/demo.gif (then: python scripts/make_demo_gif.py).
const baseURL = process.env.PIGGYBACK_URL ?? "http://localhost:8000"

export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // tests share one scenario's decisions
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  projects: [
    { name: "e2e", testIgnore: /screenshots|demo/, use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "demo", testMatch: /demo/, use: { ...devices["Desktop Chrome"], colorScheme: "light" } },
    { name: "screenshots", testMatch: /screenshots/, use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, colorScheme: "light" } },
  ],
})
