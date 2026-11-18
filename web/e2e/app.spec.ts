import { currentShipment, expect, revoke, SCENARIO, test } from "./fixtures"

test.describe("queue", () => {
  test("shows the most urgent parcel with a recommendation", async ({ page, errors }) => {
    await page.goto("/app/")
    await expect(page.getByText("Needs a decision")).toBeVisible()
    await expect(page.getByRole("listbox", { name: "Shipments" }).getByRole("option").first()).toHaveAttribute("aria-selected", "true")
    await expect(page.getByRole("button", { name: /Accept recommendation/ })).toBeVisible()
    await expect(page.getByText(/Recommended for the lowest total/)).toBeVisible()
    void errors
  })

  test("accepting with the keyboard books the recovery and moves on", async ({ page, errors }) => {
    await page.goto("/app/")
    const decided = page.getByRole("radio", { name: /^Decided \d+/ })
    await expect(decided).toBeVisible()
    const before = Number((await decided.textContent())!.replace(/\D/g, ""))
    const id = await currentShipment(page)

    await page.keyboard.press("a")
    await expect(page.getByText(`${id}:`, { exact: false }).first()).toBeVisible() // toast
    await expect(decided).toHaveText(new RegExp(`Decided ${before + 1}`))
    await expect.poll(() => currentShipment(page)).not.toBe(id)

    const decisions = await (await page.request.get(`/scenarios/${SCENARIO}/decisions`)).json()
    expect(decisions.some((d: { shipment_id: string; action: string }) => d.shipment_id === id && d.action === "ACCEPT")).toBe(true)
    await revoke(page, id)
    void errors
  })

  test("an alternative can be picked and accepted", async ({ page, errors }) => {
    await page.goto("/app/")
    const id = await currentShipment(page)
    const options = page.getByRole("region", { name: `Recovery for ${id}` }).getByRole("button", { pressed: false })
    const alt = options.filter({ hasText: /Piggyback|Dedicated truck/ }).first()
    await alt.click()
    await expect(page.getByRole("button", { name: /Accept this option/ })).toBeVisible()
    await page.getByRole("button", { name: /Accept this option/ }).click()
    await expect(page.getByText(/booked/).first()).toBeVisible()
    await revoke(page, id)
    void errors
  })

  test("dismissing a flag records a false alarm", async ({ page, errors }) => {
    await page.goto("/app/")
    const id = await currentShipment(page)
    await page.getByRole("button", { name: /Dismiss flag/ }).click()
    await expect(page.getByText(`${id} dismissed`)).toBeVisible()
    const decisions = await (await page.request.get(`/scenarios/${SCENARIO}/decisions`)).json()
    expect(decisions.find((d: { shipment_id: string }) => d.shipment_id === id)?.action).toBe("DISMISS")
    await revoke(page, id)
    void errors
  })
})

test("network map draws layers and opens a hub", async ({ page, errors }) => {
  await page.goto("/app/#/network")
  await expect(page.locator(".maplibregl-canvas")).toBeVisible()
  const hub = page.getByRole("button", { name: /^MUM, Mumbai/ })
  await expect(hub).toBeVisible()
  // Layers loaded and took their colours (regressions: oklch colours and the
  // bundled worker URL once silently left the map with markers but no lines).
  await expect(page.locator('[role="region"][aria-label="Network map"][data-map-ready="true"]')).toBeVisible()
  await hub.click()
  await expect(page.getByRole("region", { name: "Hub MUM" })).toBeVisible()
  await expect(page.getByText("Departures in the next 24 h")).toBeVisible()
  void errors
})

test("plans page shows a plan and the decision log", async ({ page, errors }) => {
  await page.goto("/app/#/plans")
  await expect(page.getByText("Saved plans")).toBeVisible()
  await expect(page.getByText("Parcels planned")).toBeVisible()
  await page.getByRole("tab", { name: "Decision log" }).click()
  await expect(page.getByRole("tabpanel")).toBeVisible()
  void errors
})

test("benchmark page shows the headline with its confidence interval", async ({ page, errors }) => {
  await page.goto("/app/#/benchmark")
  await expect(page.getByText(/paired simulated scenarios/)).toBeVisible()
  await expect(page.getByText(/95% CI .*better in \d+ of \d+ scenarios/)).toBeVisible()
  await expect(page.getByRole("img", { name: /Penalty change versus next service/ })).toBeVisible()
  await expect(page.getByRole("row", { name: /Piggyback \(risk-aware, CP-SAT\)/ })).toBeVisible()
  void errors
})

test("lab rejects an invalid scenario name before calling the API", async ({ page, errors }) => {
  await page.goto("/app/#/lab")
  const form = page.getByRole("form", { name: "Create a scenario" })
  await form.getByPlaceholder("seed7_tight").fill("Bad Name!")
  await form.getByRole("button", { name: /Generate scenario/ }).click()
  await expect(form.getByRole("alert")).toContainText("lowercase letters")
  void errors
})

test("shift replay plays a whole shift and compares both policies", async ({ page, errors }) => {
  test.slow() // the first request simulates the shift
  await page.goto("/app/#/replay")
  const slider = page.getByRole("slider", { name: "Time in the shift" })
  await expect(slider).toBeVisible({ timeout: 60_000 })
  const now = page.getByRole("region", { name: "Both policies right now" })
  const bookings = now.getByText("Bookings made").locator("xpath=following-sibling::span[1]")
  const atStart = Number(await bookings.textContent())

  await slider.fill(await slider.getAttribute("max") ?? "60") // jump to the end of the shift
  await expect.poll(async () => Number(await bookings.textContent())).toBeGreaterThan(atStart)
  await expect(page.getByRole("region", { name: "End of shift" })).toContainText(/Realized penalty/)
  await expect(page.getByRole("region", { name: "Latest events" }).getByRole("listitem").first()).toBeVisible()

  await page.getByRole("radio", { name: "Next service" }).click() // the map switches policy
  await expect(page.getByRole("region", { name: "Latest events" })).toContainText("Next service")
  await page.getByRole("button", { name: "Back to the start of the shift" }).click()
  await expect(page.getByRole("region", { name: "Latest events" })).toContainText(/Nothing yet|booked/)
  void errors
})
