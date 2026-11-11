import path from "node:path"

import { expect, test } from "./fixtures"

// `npm run screenshots`: re-captures the README images (light theme, 1440x900).
const OUT = path.resolve(import.meta.dirname, "../../docs/img")

const DEMO = "demo"

test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 (npm run screenshots)")

// A full-size scenario with a fresh plan, so the README shows the real thing.
test.beforeAll(async ({ request }) => {
  if ((await request.get(`/scenarios/${DEMO}`)).status() === 404) {
    expect((await request.post("/scenarios", { data: { scenario_id: DEMO, seed: 42 } })).ok()).toBe(true)
  }
  const plans = await (await request.get(`/scenarios/${DEMO}/plans`)).json()
  if (!plans.length) expect((await request.post(`/scenarios/${DEMO}/plans`, { data: { solver: "cpsat", grace_h: 2 } })).ok()).toBe(true)
})

test.use({ scenarioId: DEMO })

test("queue", async ({ page, errors }) => {
  await page.goto("/app/")
  await expect(page.getByText(/Recommended for the lowest total/)).toBeVisible()
  await expect(page.locator(".maplibregl-canvas")).toBeVisible()
  await page.waitForTimeout(800) // let the map settle
  await page.screenshot({ path: `${OUT}/queue.png` })
  void errors
})

test("network", async ({ page, errors }) => {
  await page.goto("/app/#/network")
  await page.getByRole("button", { name: /^MUM, Mumbai/ }).click()
  await expect(page.getByText("Departures in the next 24 h")).toBeVisible()
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/network.png` })
  void errors
})

test("benchmark", async ({ page, errors }) => {
  await page.goto("/app/#/benchmark")
  await expect(page.getByRole("img", { name: /Penalty change versus next service/ })).toBeVisible()
  await page.screenshot({ path: `${OUT}/benchmark.png` })
  void errors
})
