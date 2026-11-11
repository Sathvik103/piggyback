import fs from "node:fs"
import path from "node:path"

import { currentShipment, expect, revoke, test } from "./fixtures"

// `npm run demo`: walks the main flow on the demo scenario and saves numbered
// frames plus how long each should show; scripts/make_demo_gif.py turns them into
// docs/img/demo.gif.
const FRAMES = path.resolve(import.meta.dirname, "../demo-frames")
const DEMO = "demo"

test.skip(!process.env.DEMO, "set DEMO=1 (npm run demo)")
test.use({ scenarioId: DEMO, viewport: { width: 1280, height: 800 } })

test("demo", async ({ page, errors }) => {
  fs.rmSync(FRAMES, { recursive: true, force: true })
  fs.mkdirSync(FRAMES, { recursive: true })
  const timings: number[] = []
  const frame = async (ms: number) => {
    await page.screenshot({ path: path.join(FRAMES, `${String(timings.length).padStart(3, "0")}.png`) })
    timings.push(ms)
  }

  // 1. The queue, most urgent first, with a recommendation and its reason.
  await page.goto("/app/")
  await expect(page.getByText(/Recommended for the lowest total/)).toBeVisible()
  await expect(page.locator('[aria-label="Network map"][data-map-ready="true"]')).toBeVisible()
  await page.waitForTimeout(600)
  await frame(2600)

  // 2. Move down the queue with the keyboard.
  for (let i = 0; i < 2; i++) {
    await page.keyboard.press("j")
    await expect(page.getByText(/Recommended for the lowest total/)).toBeVisible()
    await page.waitForTimeout(500)
    await frame(1200)
  }

  // 3. Compare an alternative, then go back to the recommendation.
  await page.keyboard.press("4")
  await page.waitForTimeout(500)
  await frame(1600)
  await page.keyboard.press("1")
  await page.waitForTimeout(400)
  await frame(900)

  // 4. Accept: the parcel leaves the queue and the next one comes up.
  const id = await currentShipment(page)
  await page.keyboard.press("a")
  await expect(page.getByText(`${id}:`, { exact: false }).first()).toBeVisible()
  await page.waitForTimeout(500)
  await frame(2400)

  // 5. The network: the plan's routes, disruptions, and a hub's departures.
  await page.getByRole("link", { name: "Network" }).click()
  await expect(page.locator('[aria-label="Network map"][data-map-ready="true"]')).toBeVisible()
  await page.waitForTimeout(800)
  await frame(1800)
  await page.getByRole("button", { name: /^LKO, Lucknow/ }).click()
  await expect(page.getByText("Departures in the next 24 h")).toBeVisible()
  await page.waitForTimeout(500)
  await frame(2200)

  // 6. The benchmark: the measured result.
  await page.getByRole("link", { name: "Benchmark" }).click()
  await expect(page.getByRole("img", { name: /Penalty change versus next service/ })).toBeVisible()
  await page.waitForTimeout(500)
  await frame(3200)

  fs.writeFileSync(path.join(FRAMES, "timings.json"), JSON.stringify(timings))
  await revoke(page, id, DEMO)
  void errors
})
