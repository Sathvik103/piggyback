import { useQuery, useQueryClient } from "@tanstack/react-query"
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react"

import { api, SIGNED_OUT_EVENT, type User } from "@/lib/api"
import { SignInPage } from "@/pages/SignInPage"

type Auth = { user: User; canEdit: boolean; signOut: () => Promise<void> }
const AuthContext = createContext<Auth | null>(null)
const ME = ["me"] as const

/** Renders the sign-in page until there is a session, then the app. Any 401 from the
 *  API (an expired or revoked session) drops back to the sign-in page. */
export function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const me = useQuery({ queryKey: ME, queryFn: api.meOrNull, retry: false, staleTime: Infinity })

  const signedOut = useCallback(() => {
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" })
    qc.setQueryData(ME, null)
  }, [qc])

  useEffect(() => {
    window.addEventListener(SIGNED_OUT_EVENT, signedOut)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, signedOut)
  }, [signedOut])

  const signOut = useCallback(async () => {
    await api.logout().catch(() => undefined)
    signedOut()
  }, [signedOut])

  const value = useMemo(() => (me.data ? { user: me.data, canEdit: me.data.role === "operator", signOut } : null), [me.data, signOut])

  if (me.isPending) return <div className="app-backdrop h-dvh" aria-busy="true" />
  if (!value) return <SignInPage onSignedIn={(u) => qc.setQueryData(ME, u)} />
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): Auth {
  const a = useContext(AuthContext)
  if (!a) throw new Error("useAuth outside AuthGate")
  return a
}

export const READ_ONLY_HINT = "Read-only access: only operators can make changes."
