import { FlaskConical, Play } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { useQuery } from "@tanstack/react-query"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api } from "@/lib/api"
import { hoursText, inr, lakh, MODE_LABEL, shortTime, words } from "@/lib/format"
import { useCreatePlan, useDecisions, usePlan, usePlans, useScenarioSummary } from "@/lib/queries"
import { useScenario } from "@/lib/scenario"
import { cn } from "@/lib/utils"

const SOLVERS = [
  { value: "cpsat", label: "CP-SAT (optimal)" },
  { value: "greedy", label: "Greedy" },
  { value: "none", label: "Do nothing" },
]

export function PlansPage() {
  const { sid, grace } = useScenario()
  const plans = usePlans(sid)
  const [picked, setSelected] = useState<string | null>(null)
  const [solver, setSolver] = useState("cpsat")
  const [atRisk, setAtRisk] = useState(false)
  const create = useCreatePlan(sid)

  // Default to the newest plan.
  const list = plans.data ?? []
  const selected = list.some((p) => p.plan_id === picked) ? picked : (list.at(-1)?.plan_id ?? null)

  const run = () =>
    create.mutate(
      { solver, grace_h: grace, include_at_risk: atRisk },
      {
        onSuccess: (p) => {
          setSelected(p.plan_id)
          toast.success(`Plan ${p.plan_id} ready`, { description: `${p.status.toLowerCase()} in ${p.solve_s.toFixed(1)} s` })
        },
        onError: (e) => toast.error("Couldn't plan", { description: e.message }),
      },
    )

  return (
    <div className="grid min-h-full grid-cols-1 gap-4 p-3 sm:p-4 lg:h-full lg:min-h-0 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col gap-4">
        <section className="space-y-3 surface p-4">
          <h2 className="text-sm font-semibold">Run a plan</h2>
          <Select value={solver} onValueChange={setSolver}>
            <SelectTrigger className="w-full" aria-label="Solver"><SelectValue /></SelectTrigger>
            <SelectContent>
              {SOLVERS.map((s) => <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>)}
            </SelectContent>
          </Select>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={atRisk} onChange={(e) => setAtRisk(e.target.checked)} />
            <span>
              Also rebook at-risk parcels now
              <span className="block text-xs text-muted-foreground">Off by default: in the benchmark, acting early did no better than waiting.</span>
            </span>
          </label>
          <Button className="w-full" onClick={run} disabled={!sid || create.isPending}>
            <Play className="size-4" aria-hidden /> {create.isPending ? "Planning…" : "Run plan"}
          </Button>
          <p className="text-xs text-muted-foreground">Covers every flagged parcel without a decision, against truck space accepted recoveries haven't taken.</p>
        </section>
        <section className="max-h-64 overflow-y-auto surface lg:max-h-none lg:min-h-0 lg:flex-1">
          <h2 className="border-b px-4 py-2.5 text-sm font-semibold">Saved plans</h2>
          {plans.isLoading && <Skeleton className="m-4 h-24" />}
          {plans.data?.length === 0 && <p className="p-4 text-sm text-muted-foreground">No plans yet.</p>}
          {[...(plans.data ?? [])].reverse().map((p) => (
            <button
              key={p.plan_id}
              type="button"
              onClick={() => setSelected(p.plan_id)}
              className={cn("block w-full border-b px-4 py-2.5 text-left text-sm hover:bg-muted/60", selected === p.plan_id && "bg-muted")}
            >
              <div className="flex justify-between font-medium">
                <span>{p.plan_id} · {SOLVERS.find((s) => s.value === p.solver)?.label ?? p.solver}</span>
                <span className="tabular-nums text-muted-foreground">{lakh(p.objective_inr)}</span>
              </div>
              <div className="text-xs text-muted-foreground">
                {p.shipments} parcels · {p.by_mode.PIGGYBACK ?? 0} piggyback · {p.by_mode.DEDICATED ?? 0} truck · grace {p.grace_h} h
              </div>
            </button>
          ))}
        </section>
      </aside>

      <Tabs defaultValue="plan" className="min-h-0">
        <TabsList>
          <TabsTrigger value="plan">Plan</TabsTrigger>
          <TabsTrigger value="log">Decision log</TabsTrigger>
        </TabsList>
        <TabsContent value="plan" className="min-h-0">{selected ? <PlanDetail planId={selected} /> : <Skeleton className="h-64" />}</TabsContent>
        <TabsContent value="log"><DecisionLog /></TabsContent>
      </Tabs>
    </div>
  )
}

function PlanDetail({ planId }: { planId: string }) {
  const { sid } = useScenario()
  const summary = useScenarioSummary(sid)
  const plan = usePlan(sid, planId)
  const [mode, setMode] = useState("ALL")
  const [showTruth, setShowTruth] = useState(false)
  const truth = useQuery({ queryKey: ["eval-plan", sid, planId], queryFn: () => api.evalPlan(sid!, planId), enabled: showTruth && Boolean(sid) })

  if (!plan.data || !summary.data) return <Skeleton className="h-64" />
  const p = plan.data
  const rows = p.choices.filter((c) => mode === "ALL" || c.mode === mode)
  const onTime = p.choices.filter((c) => c.mode !== "NONE" && c.lateness_h <= 0.01).length
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Parcels planned" value={String(p.shipments)} />
        <Stat label="Planned on time" value={`${onTime}`} sub={`of ${p.shipments}`} />
        <Stat label="Money spent" value={inr(p.cost_inr)} />
        <Stat label="Objective (cost + lateness)" value={lakh(p.objective_inr)} />
        <Stat label="Solve time" value={`${p.solve_s.toFixed(2)} s`} sub={`${words(p.status)} · ${p.options_considered} options`} />
      </div>

      <section className="surface surface-dashed p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm">
            <FlaskConical className="size-4 text-muted-foreground" aria-hidden />
            <span className="font-medium">Replay against ground truth</span>
            <span className="text-muted-foreground">Lab only: uses the simulator's hidden truth, which no operator has.</span>
          </div>
          {!showTruth && <Button variant="outline" size="sm" onClick={() => setShowTruth(true)}>Replay</Button>}
        </div>
        {showTruth && (truth.data ? (
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Stranded home on time" value={`${Math.round(truth.data.kpis.on_time_rate * 100)}%`} sub={`${truth.data.kpis.on_time} of ${truth.data.kpis.stranded}`} tone="good" />
            <Stat label="Recovered late" value={String(truth.data.kpis.late)} />
            <Stat label="Failed in reality" value={String(truth.data.kpis.failed)} tone={truth.data.kpis.failed ? "critical" : undefined} />
            <Stat label="Not recovered" value={String(truth.data.kpis.unrecovered)} />
            <Stat label="Realized penalty" value={lakh(truth.data.kpis.realized_penalty_inr)} />
          </div>
        ) : <Skeleton className="mt-3 h-16" />)}
      </section>

      <section className="surface">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
          <h2 className="text-sm font-semibold">Choices</h2>
          <div className="flex flex-wrap gap-1.5">
            {["ALL", "PIGGYBACK", "DEDICATED", "LOCAL", "NONE"].map((m) => {
              const n = m === "ALL" ? p.shipments : (p.by_mode as Record<string, number>)[m] ?? 0
              if (m !== "ALL" && !n) return null
              return (
                <Button key={m} variant={mode === m ? "secondary" : "ghost"} size="sm" onClick={() => setMode(m)}>
                  {m === "ALL" ? "All" : MODE_LABEL[m]} <span className="text-muted-foreground">{n}</span>
                </Button>
              )
            })}
          </div>
        </div>
        <div className="max-h-[52vh] overflow-y-auto rounded-md focus-visible:outline-2 focus-visible:outline-ring/50" tabIndex={0} role="region" aria-label="Plan choices">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Shipment</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Route</TableHead>
                <TableHead>Arrives</TableHead>
                <TableHead className="text-right">Late</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => (
                <TableRow key={c.shipment_id}>
                  <TableCell className="font-medium">{c.shipment_id}</TableCell>
                  <TableCell>{MODE_LABEL[c.mode]}</TableCell>
                  <TableCell className="text-muted-foreground">{c.route.join(" → ")}</TableCell>
                  <TableCell className="tabular-nums">{c.delivered_h != null ? shortTime(summary.data.start_at, c.delivered_h, summary.data.now_h) : "—"}</TableCell>
                  <TableCell className={cn("text-right tabular-nums", c.lateness_h > 0.01 && "text-warn")}>{c.mode === "NONE" ? "—" : c.lateness_h > 0.01 ? hoursText(c.lateness_h) : "on time"}</TableCell>
                  <TableCell className="text-right tabular-nums">{c.cost_inr ? inr(c.cost_inr) : "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>
    </div>
  )
}

function DecisionLog() {
  const { sid } = useScenario()
  const log = useDecisions(sid, true)
  if (!log.data) return <Skeleton className="h-64" />
  if (!log.data.length) return <p className="surface p-6 text-sm text-muted-foreground">No decisions yet. Accept or dismiss parcels in the queue.</p>
  return (
    <section className="surface">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>When</TableHead>
            <TableHead>Shipment</TableHead>
            <TableHead>Decision</TableHead>
            <TableHead>Recovery</TableHead>
            <TableHead>By</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {[...log.data].reverse().map((d) => (
            <TableRow key={d.decision_id}>
              <TableCell className="tabular-nums text-muted-foreground">{new Date(d.decided_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</TableCell>
              <TableCell className="font-medium">{d.shipment_id}</TableCell>
              <TableCell>
                <Badge variant={d.action === "ACCEPT" ? "default" : d.action === "DISMISS" ? "secondary" : "outline"}>{words(d.action)}</Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">{d.option ? `${MODE_LABEL[d.option.mode]} · ${d.option.note}` : "—"}</TableCell>
              <TableCell className="text-muted-foreground">{d.decided_by}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </section>
  )
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "good" | "critical" }) {
  return (
    <div className="surface px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("text-xl font-semibold tabular-nums", tone === "good" && "text-good", tone === "critical" && "text-critical")}>{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  )
}
