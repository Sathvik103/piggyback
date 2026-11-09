import { ChartColumn, FlaskConical, Inbox, ListChecks, Map as MapIcon, Moon, RefreshCw, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { NavLink, Outlet } from "react-router"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { dayTime } from "@/lib/format"
import { useCreatePlan, useLatestPlan, useScenarioSummary, useScenarios } from "@/lib/queries"
import { useScenario } from "@/lib/scenario"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/", label: "Queue", icon: Inbox, end: true },
  { to: "/network", label: "Network", icon: MapIcon },
  { to: "/plans", label: "Plans", icon: ListChecks },
  { to: "/benchmark", label: "Benchmark", icon: ChartColumn },
  { to: "/lab", label: "Lab", icon: FlaskConical },
]

const GRACE_OPTIONS = [0.5, 1, 2, 3, 4, 6]

export function AppShell() {
  return (
    <div className="flex h-dvh min-h-0 bg-muted/30 text-foreground">
      <nav aria-label="Main" className="flex w-[72px] shrink-0 flex-col items-center gap-1 border-r bg-background py-3">
        <div className="mb-3 flex size-9 items-center justify-center rounded-lg bg-s1 text-sm font-semibold text-white" aria-hidden>
          Pb
        </div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex w-16 flex-col items-center gap-1 rounded-lg px-1 py-2 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                isActive && "bg-muted font-medium text-foreground",
              )
            }
          >
            <Icon className="size-5" aria-hidden />
            {label}
          </NavLink>
        ))}
        <div className="flex-1" />
        <ThemeToggle />
      </nav>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="min-h-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

function TopBar() {
  const { sid, setSid, grace, setGrace } = useScenario()
  const scenarios = useScenarios()
  const summary = useScenarioSummary(sid)
  const { plans } = useLatestPlan(sid)
  const createPlan = useCreatePlan(sid)
  const latest = plans.data?.at(-1)

  const replan = () =>
    createPlan.mutate(
      { solver: "cpsat", grace_h: grace },
      {
        onSuccess: (p) =>
          toast.success(`Plan ${p.plan_id} ready`, {
            description: `${p.by_mode.PIGGYBACK ?? 0} piggyback · ${p.by_mode.DEDICATED ?? 0} truck · ${p.status.toLowerCase()} in ${p.solve_s.toFixed(1)} s`,
          }),
        onError: (e) => toast.error("Couldn't plan", { description: e.message }),
      },
    )

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-background px-4 py-2.5">
      <div className="text-[15px] font-semibold tracking-tight">Piggyback</div>
      <Select value={sid ?? undefined} onValueChange={setSid}>
        <SelectTrigger size="sm" className="w-44" aria-label="Scenario">
          <SelectValue placeholder="Scenario" />
        </SelectTrigger>
        <SelectContent>
          {scenarios.data?.map((s) => (
            <SelectItem key={s.scenario_id} value={s.scenario_id}>
              {s.scenario_id}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {summary.data && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="text-sm text-muted-foreground tabular-nums">{dayTime(summary.data.start_at, summary.data.now_h)} IST</span>
          </TooltipTrigger>
          <TooltipContent>Simulated "now". Every time in the app is relative to it.</TooltipContent>
        </Tooltip>
      )}
      <div className="flex-1" />
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        Grace
        <Select value={String(grace)} onValueChange={(v) => setGrace(Number(v))}>
          <SelectTrigger size="sm" className="w-20" aria-label="Detection grace period">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {GRACE_OPTIONS.map((g) => (
              <SelectItem key={g} value={String(g)}>
                {g} h
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      <span className="text-sm text-muted-foreground">
        {latest ? (
          <>
            Plan <span className="font-medium text-foreground">{latest.plan_id}</span> · {latest.solver === "cpsat" ? "CP-SAT" : latest.solver}
            {latest.status === "OPTIMAL" ? " · optimal" : ""}
          </>
        ) : (
          "No plan yet"
        )}
      </span>
      <Button size="sm" onClick={replan} disabled={!sid || createPlan.isPending}>
        <RefreshCw className={cn("size-4", createPlan.isPending && "animate-spin")} aria-hidden />
        {createPlan.isPending ? "Planning…" : latest ? "Re-plan" : "Plan now"}
      </Button>
    </header>
  )
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme === "dark"
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={dark ? "Use light theme" : "Use dark theme"} onClick={() => setTheme(dark ? "light" : "dark")}>
          {dark ? <Sun className="size-5" /> : <Moon className="size-5" />}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">{dark ? "Light theme" : "Dark theme"}</TooltipContent>
    </Tooltip>
  )
}
