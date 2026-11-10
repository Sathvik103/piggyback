// Make sure the dedicated `e2e` scenario exists and has a plan, via the API.
// Tests never touch any other scenario.
export const SCENARIO = "e2e"

export default async function globalSetup() {
  const base = process.env.PIGGYBACK_URL ?? "http://localhost:8000"
  const json = (r: Response) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status} ${r.url}`)))
  const post = (path: string, body: unknown) =>
    fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })

  const exists = await fetch(`${base}/scenarios/${SCENARIO}`)
  if (exists.status === 404) await post("/scenarios", { scenario_id: SCENARIO, seed: 42, n_shipments: 1200 }).then(json)
  else if (!exists.ok) throw new Error(`API not reachable at ${base} (${exists.status})`)

  const plans = await fetch(`${base}/scenarios/${SCENARIO}/plans`).then(json)
  if (!plans.length) await post(`/scenarios/${SCENARIO}/plans`, { solver: "cpsat", grace_h: 2 }).then(json)
}
