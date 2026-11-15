import { LogIn } from "lucide-react"
import { useState, type FormEvent } from "react"

import { LogoMark } from "@/components/Logo"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, ApiError, type User } from "@/lib/api"

export function SignInPage({ onSignedIn }: { onSignedIn: (u: User) => void }) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onSignedIn(await api.login(username.trim(), password))
    } catch (err) {
      setPassword("")
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many failed attempts. Wait a few minutes and try again."
          : err instanceof ApiError && err.status === 401
            ? "Wrong username or password."
            : "Couldn't reach the server. Try again.",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="app-backdrop flex min-h-dvh items-center justify-center p-4">
      <form onSubmit={submit} aria-label="Sign in" className="w-full max-w-sm space-y-5 surface p-6 sm:p-8">
        <div className="flex items-center gap-3">
          <LogoMark size={44} className="drop-shadow-[0_8px_18px_rgb(42_85_162/0.35)]" />
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Sign in to Piggyback</h1>
            <p className="text-sm text-muted-foreground">Shipment recovery console</p>
          </div>
        </div>
        <label className="block space-y-1.5 text-sm font-medium">
          Username
          <Input name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required autoFocus
            value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="block space-y-1.5 text-sm font-medium">
          Password
          <Input name="password" type="password" autoComplete="current-password" required
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="text-sm text-critical" role="alert">{error}</p>}
        <Button type="submit" className="w-full" disabled={busy || !username || !password}>
          <LogIn className="size-4" aria-hidden /> {busy ? "Signing in…" : "Sign in"}
        </Button>
        <p className="text-xs text-muted-foreground">
          Accounts are created by an administrator with <code className="rounded bg-muted px-1 whitespace-nowrap">python -m db user add</code>.
        </p>
      </form>
    </main>
  )
}
