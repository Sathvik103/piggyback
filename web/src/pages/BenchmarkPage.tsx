import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { BenchmarkSummary, Paired, Stat } from "@/lib/api"
import { pct, signed, words } from "@/lib/format"
import { useBenchmark } from "@/lib/queries"
import { cn } from "@/lib/utils"

const INGREDIENTS: { key: string; label: string; what: string }[] = [
  { key: "piggyback_blind", label: "Delay-aware planning", what: "vs planning on the published timetable" },
  { key: "piggyback_greedy", label: "CP-SAT optimizer", what: "vs greedy, one parcel at a time" },
  { key: "piggyback_proactive", label: "Waiting until stranded", what: "vs rebooking at-risk parcels early" },
  { key: "piggyback_no_buffer", label: "Transfer buffer", what: "vs no slack after unobserved trucks" },
]

export function BenchmarkPage() {
  const b = useBenchmark()
  if (b.isLoading) return <div className="space-y-4 p-6"><Skeleton className="h-28" /><Skeleton className="h-64" /></div>
  if (!b.data?.available || !b.data.versus || !b.data.policies || !b.data.meta)
    return <p className="p-6 text-sm text-muted-foreground">No benchmark results yet. Run <code>python -m benchmark.run</code>.</p>
  const d = b.data
  const vs = d.versus!.next_service
  const pol = (name: string) => d.policies!.find((p) => p.name === name)
  const hero = pol(d.hero)!
  const ns = pol("next_service")!
  const m = (p: typeof hero, k: string) => (p[k] as Stat).mean ?? 0

  return (
    <div className="mx-auto max-w-5xl space-y-8 p-6">
      <header className="space-y-1 border-b pb-6">
        <p className="text-sm text-muted-foreground">
          Piggyback vs TMS-style next-service rebooking · {d.meta!.scenarios} paired simulated scenarios
        </p>
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="font-serif text-6xl tracking-tight tabular-nums">{signed(vs.penalty_pct.mean, "%")}</span>
          <span className="text-sm text-muted-foreground">
            realized penalty (money spent + value of hours late) · 95% CI {ciRange(vs.penalty_pct, "%")} · better in {vs.wins} of {vs.n} scenarios
          </span>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Tile label="Stranded parcels on time" value={signed(vs.ontime_pp.mean, " pp")} sub={`${pct(m(hero, "on_time_pct"))} vs ${pct(m(ns, "on_time_pct"))}`} />
        <Tile
          label="Failed recoveries"
          value={pct(m(hero, "failed_pct"))}
          sub={`vs ${pct(m(ns, "failed_pct"))} · ${(m(ns, "failed_pct") / Math.max(0.01, m(hero, "failed_pct"))).toFixed(1)}× fewer`}
        />
        <Tile label="Plan time" value={`${m(hero, "plan_s").toFixed(1)} s`} sub="options + CP-SAT, proven optimal" />
      </div>

      {d.robustness.length > 0 && (
        <section className="space-y-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Does the win hold under stress?</h2>
            <p className="text-sm text-muted-foreground">
              Change in realized penalty vs next service, mean and 95% CI, under conditions Piggyback wasn't designed around.
            </p>
          </div>
          <RobustnessPlot rows={d.robustness} />
        </section>
      )}

      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Where the gain comes from</h2>
          <p className="text-sm text-muted-foreground">Each bar removes or swaps one ingredient and measures how much worse Piggyback gets.</p>
        </div>
        <Ingredients versus={d.versus!} />
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold tracking-tight">All policies</h2>
        <PolicyTable data={d} />
      </section>

      {d.by_tightness && (
        <section className="space-y-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">By how full the trucks are</h2>
            <p className="text-sm text-muted-foreground">The optimizer earns its keep when spare capacity is scarce.</p>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Capacity tightness</TableHead>
                <TableHead className="text-right">Δ penalty vs next service</TableHead>
                <TableHead className="text-right">CP-SAT vs greedy</TableHead>
                <TableHead className="text-right">Δ on time vs next service</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {Object.entries(d.by_tightness).map(([t, r]) => (
                <TableRow key={t}>
                  <TableCell>{t} {Number(t) >= 0.9 ? "(nearly full)" : Number(t) <= 0.3 ? "(plenty of room)" : ""}</TableCell>
                  <TableCell className="text-right"><Delta s={r.next_service?.penalty_pct} unit="%" /></TableCell>
                  <TableCell className="text-right"><Delta s={r.piggyback_greedy?.penalty_pct} unit="%" /></TableCell>
                  <TableCell className="text-right"><Delta s={r.next_service?.ontime_pp} unit=" pp" good="up" /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      )}

      <section className="space-y-2 rounded-xl border bg-card p-5 text-sm text-muted-foreground">
        <h2 className="text-base font-semibold text-foreground">Method and caveats</h2>
        <p>
          Every policy gets the same detections on the same scenarios, so differences come from recovery alone. Each plan is replayed against the
          simulator's ground truth: where parcels really are and how late trucks really ran. Comparisons are paired per scenario with 95%
          t-intervals; a result counts as significant only if its interval excludes zero.
        </p>
        <p>
          Reactive baselines get a second chance at parcels that strand after "now". The transfer buffer was tuned on separate validation seeds.
          These are results on Piggyback's own simulator, reproducible from seeds with <code>python -m benchmark.run</code>, not evidence from a
          real network.
        </p>
      </section>
    </div>
  )
}

function RobustnessPlot({ rows }: { rows: BenchmarkSummary["robustness"] }) {
  const W = 880, rowH = 40, top = 10, left = 250, right = 90
  const lo = Math.min(...rows.map((r) => (r.vs_next_service.penalty_pct.mean ?? 0) - (r.vs_next_service.penalty_pct.ci95 ?? 0)))
  const min = Math.floor(lo / 10) * 10
  const x = (v: number) => left + ((v - min) / (0 - min)) * (W - left - right)
  const H = top + rows.length * rowH + 26
  const ticks = Array.from({ length: Math.round(-min / 10) + 1 }, (_, i) => min + i * 10)
  return (
    <figure className="overflow-x-auto rounded-xl border bg-card p-4">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[620px]" role="img" aria-label="Penalty change versus next service under each stress condition, with 95 percent confidence intervals">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={top} y2={H - 22} className={t === 0 ? "stroke-muted-foreground" : "stroke-border"} strokeDasharray={t === 0 ? undefined : "2 3"} />
            <text x={x(t)} y={H - 6} textAnchor="middle" className="fill-muted-foreground text-[12px]">{t === 0 ? "0%" : `−${-t}%`}</text>
          </g>
        ))}
        {rows.map((r, i) => {
          const y = top + i * rowH + rowH / 2
          const s = r.vs_next_service.penalty_pct
          const mean = s.mean ?? 0, h = s.ci95 ?? 0
          return (
            <g key={r.variant}>
              <title>{`${words(r.variant)}: ${signed(mean, "%")} (±${h.toFixed(1)}), wins ${r.vs_next_service.wins}/${r.vs_next_service.n}`}</title>
              <text x={0} y={y - 3} className="fill-foreground text-[13px]">{words(r.variant)}</text>
              <text x={0} y={y + 12} className="fill-muted-foreground text-[11px]">{r.note}</text>
              <line x1={x(mean - h)} x2={x(Math.min(0, mean + h))} y1={y} y2={y} className="stroke-s1" strokeWidth={2} strokeLinecap="round" />
              <circle cx={x(mean)} cy={y} r={5.5} className="fill-s1 stroke-card" strokeWidth={2} />
              <text x={W - right + 12} y={y + 4} className="fill-foreground text-[13px] tabular-nums">{signed(mean, "%")}</text>
            </g>
          )
        })}
      </svg>
      <figcaption className="mt-2 text-xs text-muted-foreground">
        Failed recoveries, Piggyback vs next service:{" "}
        {rows.map((r) => `${words(r.variant)} ${r.failed_pct.hero.toFixed(1)}% vs ${r.failed_pct.next_service.toFixed(1)}%`).join(" · ")}
      </figcaption>
    </figure>
  )
}

function Ingredients({ versus }: { versus: Record<string, Paired> }) {
  const present = INGREDIENTS.filter((i) => versus[i.key])
  const max = Math.max(...present.map((i) => Math.abs(versus[i.key].penalty_pct.mean ?? 0)), 1)
  return (
    <div className="space-y-2.5 rounded-xl border bg-card p-5">
      {present.map((i) => {
        const s = versus[i.key].penalty_pct
        const v = s.mean ?? 0
        const helps = v < 0
        return (
          <div key={i.key} className="grid grid-cols-[220px_minmax(0,1fr)_120px] items-center gap-3 text-sm">
            <div>
              <div className="font-medium">{i.label}</div>
              <div className="text-xs text-muted-foreground">{i.what}</div>
            </div>
            <div className="h-4 rounded-r bg-muted/60">
              <div
                className={cn("h-4 rounded-r", s.significant && helps ? "bg-s1" : "bg-s1/30")}
                style={{ width: `${(Math.abs(v) / max) * 100}%` }}
              />
            </div>
            <div className="text-right tabular-nums">
              {signed(v, "%")}
              <span className="ml-1 text-xs text-muted-foreground">{s.significant ? `±${(s.ci95 ?? 0).toFixed(1)}` : "not significant"}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function PolicyTable({ data }: { data: BenchmarkSummary }) {
  const cols: [string, string, (s: Stat) => string][] = [
    ["on_time_pct", "On time", (s) => pct(s.mean)],
    ["failed_pct", "Failed", (s) => pct(s.mean)],
    ["unrecovered_pct", "Unrecovered", (s) => pct(s.mean)],
    ["cost_lakh", "Spent (₹ lakh)", (s) => (s.mean ?? 0).toFixed(2)],
    ["penalty_lakh", "Penalty (₹ lakh)", (s) => (s.mean ?? 0).toFixed(2)],
  ]
  return (
    <div className="overflow-x-auto rounded-xl border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Policy</TableHead>
            {cols.map(([, label]) => <TableHead key={label} className="text-right">{label}</TableHead>)}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.policies!.map((p) => (
            <TableRow key={p.name} className={cn(p.name === data.hero && "bg-s1/5 font-medium")}>
              <TableCell>
                {p.label}
                <span className="ml-2 text-xs font-normal text-muted-foreground">{p.family}</span>
              </TableCell>
              {cols.map(([k, label, f]) => <TableCell key={label} className="text-right tabular-nums">{f(p[k] as Stat)}</TableCell>)}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-xl border bg-card px-5 py-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="text-xs text-muted-foreground">{sub}</div>
    </div>
  )
}

function Delta({ s, unit, good = "down" }: { s?: Stat; unit: string; good?: "up" | "down" }) {
  if (!s || s.mean == null) return <span className="text-muted-foreground">—</span>
  const better = good === "down" ? s.mean < 0 : s.mean > 0
  return (
    <span className={cn("tabular-nums", s.significant ? (better ? "font-medium text-foreground" : "text-critical") : "text-muted-foreground")}>
      {signed(s.mean, unit)}
      {!s.significant && <span className="ml-1 text-xs">ns</span>}
    </span>
  )
}

function ciRange(s: Stat, unit: string) {
  if (s.mean == null || s.ci95 == null) return "—"
  return `${signed(s.mean - s.ci95, unit)} to ${signed(s.mean + s.ci95, unit)}`
}
