import { expect, storageFor, test, USERS } from "./fixtures"

test.describe("signed out", () => {
  test.use({ storageState: { cookies: [], origins: [] }, signedOutErrorsOk: true })

  test("sign in, see who you are, sign out", async ({ page, errors }) => {
    await page.goto("/app/")
    const form = page.getByRole("form", { name: "Sign in" })
    await expect(form).toBeVisible()
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0) // nothing behind it

    await form.getByLabel("Username").fill(USERS.operator)
    await form.getByLabel("Password").fill("not the password")
    await form.getByRole("button", { name: "Sign in" }).click()
    await expect(page.getByRole("alert")).toHaveText("Wrong username or password.")
    await expect(form.getByLabel("Password")).toHaveValue("")

    await form.getByLabel("Password").fill(process.env.E2E_OPERATOR_PASSWORD!)
    await form.getByRole("button", { name: "Sign in" }).click()
    await expect(page.getByText("Needs a decision")).toBeVisible()

    await page.getByRole("button", { name: /account: E2E Operator$/ }).click()
    await expect(page.getByRole("menu")).toContainText("e2e-operator · Operator")
    await page.getByRole("menuitem", { name: "Sign out" }).click()
    await expect(page.getByRole("form", { name: "Sign in" })).toBeVisible()
    expect((await page.request.get("/auth/me")).status()).toBe(401) // the session is gone server-side
    void errors
  })
})

test.describe("viewer", () => {
  test.use({ storageState: storageFor("viewer") })

  test("can look but not change anything", async ({ page, errors }) => {
    await page.goto("/app/")
    const card = page.getByRole("region", { name: /^Recovery for / })
    await expect(card).toBeVisible()
    await expect(card.getByRole("button", { name: /Accept/ })).toBeDisabled()
    await expect(card.getByRole("button", { name: /Dismiss flag/ })).toBeDisabled()
    await expect(card.getByText("Read-only access")).toBeVisible()
    await expect(page.getByRole("button", { name: /Re-plan|Plan now/ })).toBeDisabled()

    const before = await (await page.request.get("/scenarios/e2e/decisions")).json()
    await page.keyboard.press("a") // the shortcut is off too
    await page.waitForTimeout(500)
    expect(await (await page.request.get("/scenarios/e2e/decisions")).json()).toEqual(before)
    void errors
  })
})
