import { test as base, expect, type Page } from "@playwright/test"

import { SCENARIO, storageFor, USERS } from "./global-setup"

// Every test: open `scenarioId` (the e2e scenario unless a spec overrides it), and
// fail on any uncaught error or console error.
// `signedOutErrorsOk`: a signed-out page logs the API's 401s to the console by design.
export const test = base.extend<{ scenarioId: string; signedOutErrorsOk: boolean; errors: string[] }>({
  scenarioId: [SCENARIO, { option: true }],
  signedOutErrorsOk: [false, { option: true }],
  errors: async ({ page, scenarioId, signedOutErrorsOk }, provide) => {
    const errors: string[] = []
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`))
    page.on("console", (m) => {
      if (m.type() !== "error") return
      if (signedOutErrorsOk && /status of 401/.test(m.text())) return
      errors.push(`console: ${m.text()}`)
    })
    await page.addInitScript((sid) => localStorage.setItem("piggyback.sid", sid), scenarioId)
    await provide(errors)
    expect(errors, "no errors in the browser").toEqual([])
  },
})

export { expect, SCENARIO, storageFor, USERS }

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
