import AxeBuilder from "@axe-core/playwright"

import { expect, test } from "./fixtures"

// Automated WCAG 2.1 AA checks on every page, in light and dark. The map canvas is
// excluded: it is a WebGL picture with its own text alternative (the hub list).
const PAGES = [
  { name: "queue", path: "/app/", ready: "Needs a decision" },
  { name: "network", path: "/app/#/network", ready: "Network" },
  { name: "plans", path: "/app/#/plans", ready: "Plans" },
  { name: "benchmark", path: "/app/#/benchmark", ready: "Benchmark" },
  { name: "lab", path: "/app/#/lab", ready: "Lab" },
]

for (const scheme of ["light", "dark"] as const) {
  test.describe(`accessibility (${scheme})`, () => {
    test.use({ colorScheme: scheme })
    for (const p of PAGES) {
      test(`${p.name} has no WCAG A/AA violations`, async ({ page, errors }) => {
        await page.goto(p.path)
        await expect(page.getByRole("main")).toBeVisible()
        await expect(page.getByText(p.ready).first()).toBeVisible()
        await page.waitForLoadState("networkidle")
        const result = await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
          .exclude(".maplibregl-canvas")
          .analyze()
        const summary = result.violations.map((v) =>
          `${v.id} (${v.impact}): ${v.help}\n` + v.nodes.slice(0, 5).map((n) => `    ${n.target.join(" ")} ${n.failureSummary?.split("\n")[1] ?? ""}`).join("\n"))
        expect(summary, "axe violations").toEqual([])
        void errors
      })
    }
  })
}
