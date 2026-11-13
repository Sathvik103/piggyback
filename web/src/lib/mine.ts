// Decisions and plans made in this tab, so their echo from the live event stream
// refreshes data without announcing our own work back to us.
const decisions = new Set<number>()
const plans = new Set<string>()

export const mine = {
  addDecision: (id: number) => void decisions.add(id),
  addPlan: (id: string) => void plans.add(id),
  /** True (once) if this decision was made here. */
  takeDecision: (id: number) => decisions.delete(id),
  /** True (once) if this plan was made here. */
  takePlan: (id: string) => plans.delete(id),
}
