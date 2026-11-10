import { test as base, expect, type Page } from "@playwright/test"

import { SCENARIO } from "./global-setup"

// Every test: open `scenarioId` (the e2e scenario unless a spec overrides it), and
// fail on any uncaught error or console error.
export const test = base.extend<{ scenarioId: string; errors: string[] }>({
  scenarioId: [SCENARIO, { option: true }],
  errors: async ({ page, scenarioId }, provide) => {
    const errors: string[] = []
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`))
    page.on("console", (m) => m.type() === "error" && errors.push(`console: ${m.text()}`))
    await page.addInitScript((sid) => localStorage.setItem("piggyback.sid", sid), scenarioId)
    await provide(errors)
    expect(errors, "no errors in the browser").toEqual([])
  },
})

export { expect, SCENARIO }

/** The shipment shown in the recovery card. */
export async function currentShipment(page: Page): Promise<string> {
  const heading = page.getByRole("region", { name: /^Recovery for / })
  await expect(heading).toBeVisible()
  const label = (await heading.getAttribute("aria-label"))!
  return label.replace("Recovery for ", "")
}

/** Undo a decision so tests stay repeatable. */
export async function revoke(page: Page, shipmentId: string, scenario = SCENARIO) {
  const r = await page.request.post(`/scenarios/${scenario}/decisions`, { data: { shipment_id: shipmentId, action: "REVOKE" } })
  expect([201, 409]).toContain(r.status())
}
