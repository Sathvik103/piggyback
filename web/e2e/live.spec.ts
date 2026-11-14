import { currentShipment, expect, SCENARIO, storageFor, test } from "./fixtures"

// Two operators on the same scenario: one decides, the other's console updates live.
test("a decision in one session shows up in another without a refresh", async ({ page, browser, errors }) => {
  const other = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState: storageFor("operator") })
  await other.addInitScript((sid) => localStorage.setItem("piggyback.sid", sid), SCENARIO)
  const b = await other.newPage()
  try {
    await Promise.all([page.goto("/app/"), b.goto("/app/")])
    for (const p of [page, b]) await expect(p.locator('[data-live="live"]')).toBeVisible()

    const decidedB = b.getByRole("radio", { name: /^Decided \d+/ })
    await expect(decidedB).toBeVisible()
    const before = Number((await decidedB.textContent())!.replace(/\D/g, ""))

    // Operator A accepts the top parcel.
    const id = await currentShipment(page)
    await page.keyboard.press("a")
    await expect(page.getByText(`${id}:`, { exact: false }).first()).toBeVisible()

    // Operator B hears about it and their queue moves on, with no reload.
    await expect(b.getByText(`${id} accepted in another session`)).toBeVisible()
    await expect(decidedB).toHaveText(new RegExp(`Decided ${before + 1}`))
    await expect(b.getByRole("listbox", { name: "Shipments" }).locator(`[data-id="${id}"]`)).toHaveCount(0)

    // A undoes it from B's side; A is told, and B is not told about its own change.
    const r = await b.request.post(`/scenarios/${SCENARIO}/decisions`, { data: { shipment_id: id, action: "REVOKE" } })
    expect(r.status()).toBe(201)
    await expect(page.getByText(`${id} reopened in another session`)).toBeVisible()
    await expect(page.getByText(`${id} accepted in another session`)).toHaveCount(0)
  } finally {
    await other.close()
  }
  void errors
})
