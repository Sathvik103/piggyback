import { ChartColumn, FlaskConical, Inbox, ListChecks, LogOut, Map as MapIcon, Moon, RefreshCw, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { NavLink, Outlet } from "react-router"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { READ_ONLY_HINT, useAuth } from "@/lib/auth"
import { dayTime } from "@/lib/format"
import { type LiveStatus, useLiveUpdates } from "@/lib/live"
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
    <div className="app-backdrop flex h-dvh min-h-0 flex-col text-foreground md:flex-row">
      <nav aria-label="Main" className="glass-bar hidden w-[76px] shrink-0 flex-col items-center gap-1.5 border-r border-border/60 py-3 md:flex">
        <div
          className="mb-4 flex size-9 items-center justify-center rounded-xl bg-gradient-to-b from-[#5b87d4] to-[#2f5ea8] text-[13px] font-semibold tracking-tight text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.35),0_4px_12px_-4px_rgb(47_94_168/0.55)]"
          aria-hidden
        >
          Pb
        </div>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex w-[62px] flex-col items-center gap-1 rounded-xl px-1 py-2 text-[11px] text-muted-foreground transition-all duration-200 hover:bg-foreground/[0.04] hover:text-foreground",
                isActive &&
                  "surface font-medium text-foreground !rounded-xl",
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
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="min-h-0 flex-1 overflow-auto focus-visible:outline-none" tabIndex={0}>
          <Outlet />
        </main>
      </div>
      <BottomNav />
    </div>
  )
}

/** Phones: the same destinations as a tab bar along the bottom edge. */
function BottomNav() {
  return (
    <nav aria-label="Main" className="glass-bar grid shrink-0 grid-cols-5 border-t border-border/60 px-1 pt-1 pb-[max(0.25rem,env(safe-area-inset-bottom))] md:hidden">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            cn("flex flex-col items-center gap-0.5 rounded-lg py-1.5 text-[11px] text-muted-foreground transition-colors", isActive && "font-medium text-foreground")
          }
        >
          {({ isActive }) => (
            <>
              <span className={cn("flex h-7 w-12 items-center justify-center rounded-full transition-colors", isActive && "bg-foreground/[0.07]")}>
                <Icon className="size-5" aria-hidden />
              </span>
              {label}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  )
}

function TopBar() {
  const { sid, setSid, grace, setGrace } = useScenario()
  const scenarios = useScenarios()
  const summary = useScenarioSummary(sid)
  const { plans } = useLatestPlan(sid)
  const createPlan = useCreatePlan(sid)
  const latest = plans.data?.at(-1)
  const live = useLiveUpdates(sid)
  const { canEdit } = useAuth()

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
    <header className="glass-bar sticky top-0 z-20 flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/60 px-4 py-2.5 md:gap-x-4 md:px-5">
      <div className="text-[15px] font-semibold tracking-tight">Piggyback</div>
      <Select value={sid ?? undefined} onValueChange={setSid}>
        <SelectTrigger size="sm" className="w-32 sm:w-44" aria-label="Scenario">
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
      <LiveBadge status={live} />
      <div className="hidden flex-1 md:block" />
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className="hidden sm:inline">Grace</span>
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
      <span className="hidden text-sm text-muted-foreground lg:inline">
        {latest ? (
          <>
            Plan <span className="font-medium text-foreground">{latest.plan_id}</span> · {latest.solver === "cpsat" ? "CP-SAT" : latest.solver}
            {latest.status === "OPTIMAL" ? " · optimal" : ""}
          </>
        ) : (
          "No plan yet"
        )}
      </span>
      <Button size="sm" onClick={replan} disabled={!sid || createPlan.isPending || !canEdit} title={canEdit ? undefined : READ_ONLY_HINT}>
        <RefreshCw className={cn("size-4", createPlan.isPending && "animate-spin")} aria-hidden />
        {createPlan.isPending ? "Planning…" : latest ? "Re-plan" : "Plan now"}
      </Button>
      <div className="md:hidden">
        <ThemeToggle />
      </div>
      <UserMenu />
    </header>
  )
}

function UserMenu() {
  const { user, signOut } = useAuth()
  const initials = user.display_name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="rounded-full" aria-label={`${initials}, account: ${user.display_name}`}>
          <span className="flex size-7 items-center justify-center rounded-full bg-foreground/[0.07] text-[11px] font-semibold">{initials}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="font-normal">
          <div className="text-sm font-medium">{user.display_name}</div>
          <div className="text-xs text-muted-foreground">
            {user.username} · {user.role === "operator" ? "Operator" : "Viewer (read-only)"}
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void signOut()}>
          <LogOut className="size-4" aria-hidden /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

const LIVE_TEXT: Record<LiveStatus, { label: string; hint: string; dot: string }> = {
  live: { label: "Live", hint: "Decisions and plans from other sessions appear as they happen.", dot: "bg-good" },
  connecting: { label: "Connecting", hint: "Reconnecting to live updates. The view refreshes once connected.", dot: "bg-warn animate-pulse" },
  offline: { label: "Offline", hint: "Live updates are off. Other sessions' decisions show after a refresh.", dot: "bg-muted-foreground" },
}

function LiveBadge({ status }: { status: LiveStatus }) {
  const t = LIVE_TEXT[status]
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span role="status" aria-label={`Live updates: ${t.label}`} className="flex items-center gap-1.5 text-xs text-muted-foreground" data-live={status}>
          <span className={cn("size-1.5 rounded-full", t.dot)} aria-hidden />
          <span className="hidden sm:inline">{t.label}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>{t.hint}</TooltipContent>
    </Tooltip>
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
