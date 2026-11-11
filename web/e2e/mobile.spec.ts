import AxeBuilder from "@axe-core/playwright"

import { currentShipment, expect, revoke, test } from "./fixtures"

// Phone layout (Pixel 7, touch): bottom tab bar, stacked panels, no sideways scrolling.
const PAGES = [
  { tab: "Queue", path: "/app/", ready: "Needs a decision" },
  { tab: "Network", path: "/app/#/network", ready: "Known disruptions" },
  { tab: "Plans", path: "/app/#/plans", ready: "Run a plan" },
  { tab: "Benchmark", path: "/app/#/benchmark", ready: "Does the win hold under stress?" },
  { tab: "Lab", path: "/app/#/lab", ready: "Generate scenario" },
]

test("the bottom tab bar reaches every page, none scrolls sideways and all pass axe", async ({ page, errors }) => {
  await page.goto("/app/")
  const tabs = page.getByRole("navigation", { name: "Main" })
  await expect(tabs).toHaveCount(1) // the desktop rail is hidden, not just covered
  for (const p of PAGES) {
    await tabs.getByRole("link", { name: p.tab }).tap()
    await expect(page).toHaveURL(new RegExp(p.path.replace("/app/", "").replace(/[#/]/g, "\\$&") + "$"))
    await expect(page.getByText(p.ready).first()).toBeVisible()
    await page.waitForLoadState("networkidle")
    const [scroll, width] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth])
    expect(scroll, `${p.tab} fits the screen`).toBeLessThanOrEqual(width)
    const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).exclude(".maplibregl-canvas").analyze()
    expect(axe.violations.map((v) => `${p.tab}: ${v.id} ${v.nodes[0]?.target.join(" ")}`)).toEqual([])
  }
  void errors
})

test("tapping a parcel brings its recovery card into view, and it can be accepted by touch", async ({ page, errors }) => {
  await page.goto("/app/")
  await expect(page.getByText("J").filter({ hasText: /^J$/ })).toBeHidden() // no keyboard hints on touch
  const rows = page.getByRole("listbox", { name: "Shipments" }).getByRole("option")
  await rows.nth(1).tap()
  const id = (await rows.nth(1).getAttribute("data-id"))!
  const card = page.getByRole("region", { name: `Recovery for ${id}` })
  await expect(card).toBeInViewport()
  expect(await currentShipment(page)).toBe(id)

  await card.getByRole("button", { name: /Accept recommendation/ }).tap()
  await expect(page.getByText(`${id}:`, { exact: false }).first()).toBeVisible()
  await revoke(page, id)
  void errors
})

test("the map legend folds away, and tapping a hub shows its panel", async ({ page, errors }) => {
  await page.goto("/app/#/network")
  await expect(page.locator("[data-map-ready]")).toBeAttached()
  const legend = page.locator("#map-legend")
  await expect(legend).toBeHidden()
  await page.getByRole("button", { name: "Legend" }).tap()
  await expect(legend).toBeVisible()
  await page.getByRole("button", { name: "Hide legend" }).tap()
  await expect(legend).toBeHidden()

  await page.getByRole("button", { name: /^HYD, Hyderabad/ }).tap()
  await expect(page.getByRole("region", { name: "Hub HYD" })).toBeInViewport()
  void errors
})
