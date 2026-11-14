// Before the tests: create the e2e users (fresh random passwords every run, so no
// credentials live in the repo), sign each in and save its session for the tests,
// and make sure the dedicated `e2e` scenario exists and has a plan.
// Tests never touch any other scenario.
import { execFileSync } from "node:child_process"
import { randomBytes } from "node:crypto"
import { mkdirSync, writeFileSync } from "node:fs"
import path from "node:path"

export const SCENARIO = "e2e"
export const AUTH_DIR = path.join(import.meta.dirname, ".auth")
export const USERS = { operator: "e2e-operator", viewer: "e2e-viewer" } as const
export const storageFor = (role: keyof typeof USERS) => path.join(AUTH_DIR, `${role}.json`)

const REPO_ROOT = path.resolve(import.meta.dirname, "..", "..")
const PYTHON = process.env.PYTHON ?? "python"

function resetUser(username: string, role: string): string {
  const password = randomBytes(18).toString("base64url")
  const run = (args: string[], input?: string) =>
    execFileSync(PYTHON, ["-m", "db", "user", ...args], { cwd: REPO_ROOT, input, stdio: ["pipe", "ignore", "pipe"] })
  try {
    run(["remove", username])
  } catch {
    /* not there yet */
  }
  run(["add", username, "--role", role, "--name", role === "operator" ? "E2E Operator" : "E2E Viewer", "--password-stdin"], password + "\n")
  return password
}

export default async function globalSetup() {
  const base = process.env.PIGGYBACK_URL ?? "http://localhost:8000"
  mkdirSync(AUTH_DIR, { recursive: true })
  const cookies: Record<string, string> = {}

  for (const [role, username] of Object.entries(USERS)) {
    const password = resetUser(username, role)
    process.env[`E2E_${role.toUpperCase()}_PASSWORD`] = password // for the sign-in tests (workers inherit env)
    const res = await fetch(`${base}/auth/login`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password }),
    })
    if (!res.ok) throw new Error(`sign-in for ${username} failed: ${res.status} (is the API at ${base} running?)`)
    const token = /pb_session=([^;]+)/.exec(res.headers.get("set-cookie") ?? "")?.[1]
    if (!token) throw new Error("no session cookie")
    cookies[role] = token
    const url = new URL(base)
    writeFileSync(storageFor(role as keyof typeof USERS), JSON.stringify({
      cookies: [{ name: "pb_session", value: token, domain: url.hostname, path: "/", expires: -1, httpOnly: true, secure: false, sameSite: "Lax" }],
      origins: [],
    }))
  }

  const headers = { "Content-Type": "application/json", Cookie: `pb_session=${cookies.operator}` }
  const json = (r: Response) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status} ${r.url}`)))
  const exists = await fetch(`${base}/scenarios/${SCENARIO}`, { headers })
  if (exists.status === 404) {
    await fetch(`${base}/scenarios`, { method: "POST", headers, body: JSON.stringify({ scenario_id: SCENARIO, seed: 42, n_shipments: 1200 }) }).then(json)
  } else if (!exists.ok) throw new Error(`API not reachable at ${base} (${exists.status})`)

  const plans = await fetch(`${base}/scenarios/${SCENARIO}/plans`, { headers }).then(json)
  if (!plans.length) await fetch(`${base}/scenarios/${SCENARIO}/plans`, { method: "POST", headers, body: JSON.stringify({ solver: "cpsat", grace_h: 2 }) }).then(json)
}
