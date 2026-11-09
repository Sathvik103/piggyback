import { createContext, useContext, useMemo, useState, type ReactNode } from "react"

import { useScenarios } from "@/lib/queries"

interface ScenarioState {
  sid: string | null
  setSid: (sid: string) => void
  grace: number
  setGrace: (g: number) => void
}

const Ctx = createContext<ScenarioState | null>(null)

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function store(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* private mode etc.: the choice just isn't remembered */
  }
}

export function ScenarioProvider({ children }: { children: ReactNode }) {
  const scenarios = useScenarios()
  const [chosen, setSidState] = useState<string | null>(() => stored("piggyback.sid"))
  const [grace, setGraceState] = useState<number>(() => Number(stored("piggyback.grace") ?? 2) || 2)

  // Fall back to the first scenario if the remembered one is gone.
  const list = scenarios.data
  const sid = list && !list.some((s) => s.scenario_id === chosen) ? (list[0]?.scenario_id ?? null) : chosen

  const value = useMemo<ScenarioState>(
    () => ({
      sid,
      setSid: (s) => {
        setSidState(s)
        store("piggyback.sid", s)
      },
      grace,
      setGrace: (g) => {
        setGraceState(g)
        store("piggyback.grace", String(g))
      },
    }),
    [sid, grace],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useScenario(): ScenarioState {
  const v = useContext(Ctx)
  if (!v) throw new Error("useScenario outside ScenarioProvider")
  return v
}
