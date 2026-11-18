import { CircleAlert, FlaskConical, Pause, Play, RotateCcw } from "lucide-react"
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react"

import { PageHeader } from "@/components/Metric"
import type { Hop } from "@/components/NetworkMap"
import { Button } from "@/components/ui/button"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { ShiftEvent, ShiftReplay, ShiftRun } from "@/lib/api"
import { dayTime, inr, lakh, MODE_LABEL } from "@/lib/format"
import { useShiftReplay } from "@/lib/queries"
import { cn } from "@/lib/utils"

const NetworkMap = lazy(() => import("@/components/NetworkMap").then((m) => ({ default: m.NetworkMap })))

const HERO = "piggyback"
const BASE = "next_service"
const SHORT: Record<string, string> = { [HERO]: "Piggyback", [BASE]: "Next service" }
const SPEEDS = [1, 3, 6] // simulated hours per second

/** Everything about one policy at time `t`. */
function stateAt(run: ShiftRun, t: number) {
  let booked = 0, broke = 0, spent = 0
  const live = new Map<string, ShiftEvent>()
  for (const e of run.events) {
    if (e.t > t) break
    if (e.event === "broke") {
      broke++
      live.delete(e.shipment_id)
    } else {
      booked++
      spent += e.cost_inr ?? 0
      live.set(e.shipment_id, e)
    }
  }
  // In flight: booked, not known to have broken, not delivered yet.
  const active = [...live.values()].filter((e) => e.planned_delivery_h == null || t < e.planned_delivery_h)
  const row = run.timeline.findLast((r) => r.t <= t) ?? run.timeline[0]
  return { booked, broke, spent, active, open: row?.open ?? 0, flagged: row?.flagged ?? 0 }
}

export function ReplayPage() {
  const [seed, setSeed] = useState(1)
  const [tightness, setTightness] = useState(0.6)
  const q = useShiftReplay(seed, tightness)

  return (
    <div className="space-y-4 p-3 sm:p-4">
      <PageHeader
        title="Shift replay"
        description="A whole shift, worked hour by hour. Both policies face the same day: parcels strand, trucks run late, bookings break and get rebooked."
      >
        <Select value={String(seed)} onValueChange={(v) => setSeed(Number(v))}>
          <SelectTrigger size="sm" className="w-28" aria-label="Day (seed)"><SelectValue /></SelectTrigger>
          <SelectContent>
            {Array.from({ length: 10 }, (_, i) => i + 1).map((s) => <SelectItem key={s} value={String(s)}>Day {s}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={String(tightness)} onValueChange={(v) => setTightness(Number(v))}>
          <SelectTrigger size="sm" className="w-36" aria-label="Truck capacity"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="0.6">Normal capacity</SelectItem>
            <SelectItem value="0.9">Tight capacity</SelectItem>
          </SelectContent>
        </Select>
      </PageHeader>
      {q.isPending ? (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
          <div className="flex h-[60dvh] flex-col items-center justify-center gap-3 surface text-sm text-muted-foreground" role="status">
            <Skeleton className="h-2 w-48" />
            Simulating a 24-hour shift for both policies (a few seconds the first time)…
          </div>
          <Skeleton className="h-[60dvh]" />
        </div>
      ) : q.isError ? (
        <p className="flex items-center gap-2 surface p-4 text-sm text-critical" role="alert"><CircleAlert className="size-4" /> Couldn't run the shift: {q.error.message}</p>
      ) : (
        <Replay key={`${seed}-${tightness}`} data={q.data} />
      )}
    </div>
  )
}

function Replay({ data }: { data: ShiftReplay }) {
  const { start_h: start, end_h: end, start_at: startAt } = data.config
  const [t, setT] = useState(start)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(3)
  const [shown, setShown] = useState<string>(HERO)
  const raf = useRef(0)

  useEffect(() => {
    if (!playing) return
    let last = performance.now()
    const tick = (now: number) => {
      raf.current = requestAnimationFrame(tick)
      if (now - last < 100) return // 10 updates a second: smooth enough, and leaves room for navigation
      const dt = (now - last) / 1000
      last = now
      setT((x) => {
        const next = Math.min(end, x + dt * speed)
        if (next >= end) setPlaying(false)
        return next
      })
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [playing, speed, end])

  const hero = data.policies[HERO], base = data.policies[BASE]
  const now: Record<string, ReturnType<typeof stateAt>> = { [HERO]: stateAt(hero, t), [BASE]: stateAt(base, t) }
  const view = now[shown]

  const { hops, dedicated, counts } = useMemo(() => {
    const h = new Map<string, Hop>()
    const ded: string[][] = []
    const c: Record<string, number> = {}
    for (const e of view.active) {
      c[e.from_hub!] = (c[e.from_hub!] ?? 0) + 1
      if (e.mode === "DEDICATED") ded.push([e.from_hub!, e.to_hub!])
      else {
        const r = e.route ?? []
        for (let i = 0; i + 1 < r.length; i++) {
          const k = `${r[i]}>${r[i + 1]}`
          const hop = h.get(k) ?? { a: r[i], b: r[i + 1], n: 0 }
          hop.n++
          h.set(k, hop)
        }
      }
    }
    return { hops: [...h.values()], dedicated: ded, counts: c }
  }, [view.active])

  const live = data.disruptions.filter((d) => d.start_h <= t && t < d.end_h)
  const closed = useMemo(() => new Set(live.filter((d) => d.kind === "HUB_CLOSURE").map((d) => d.hub_id!)), [live])
  const delayed = live.filter((d) => d.kind === "LANE_DELAY").map((d) => {
    const [from, to] = d.lane_id!.split("-")
    return { from, to, label: `+${d.delay_h} h` }
  })
  const strandedSoFar = data.strandings.filter((x) => x <= t).length
  const feed = useMemo(() => data.policies[shown].events.filter((e) => e.t <= t).slice(-7).reverse(), [data, shown, t])

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="flex min-w-0 flex-col gap-4">
        <section className="flex flex-wrap items-center gap-3 surface px-4 py-3" aria-label="Playback">
          <Button size="icon" variant={playing ? "secondary" : "default"} aria-label={playing ? "Pause" : "Play"}
            onClick={() => { if (t >= end) setT(start); setPlaying((p) => !p) }}>
            {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
          </Button>
          <Button size="icon" variant="ghost" aria-label="Back to the start of the shift" onClick={() => { setPlaying(false); setT(start) }}>
            <RotateCcw className="size-4" />
          </Button>
          <div className="min-w-[160px] flex-1">
            <input
              type="range" min={start} max={end} step={0.25} value={t} aria-label="Time in the shift"
              aria-valuetext={dayTime(startAt, t)}
              onChange={(e) => { setPlaying(false); setT(Number(e.target.value)) }}
              className="pb-range w-full"
            />
            <div className="flex justify-between text-[11px] text-muted-foreground tabular-nums">
              <span>{dayTime(startAt, start)}</span>
              <span>{dayTime(startAt, end)}</span>
            </div>
          </div>
          <div className="text-right">
            <div className="text-sm font-semibold tabular-nums">{dayTime(startAt, t)}</div>
            <div className="text-[11px] text-muted-foreground">hour {Math.floor(t - start)} of {end - start}</div>
          </div>
          <ToggleGroup type="single" size="sm" variant="outline" value={String(speed)} onValueChange={(v) => v && setSpeed(Number(v))} aria-label="Playback speed">
            {SPEEDS.map((s) => <ToggleGroupItem key={s} value={String(s)} aria-label={`${s} hours per second`}>{s}×</ToggleGroupItem>)}
          </ToggleGroup>
        </section>

        <section className="relative h-[58dvh] min-h-[380px] overflow-hidden surface" aria-label="Shift map">
          <Suspense fallback={<Skeleton className="h-full w-full" />}>
            <NetworkMap geo={data.network} counts={counts} closedHubs={closed} hops={hops} dedicated={dedicated} delayedLanes={delayed} />
          </Suspense>
          <div className="absolute top-3 left-3 flex items-center gap-2">
            <ToggleGroup type="single" size="sm" variant="outline" value={shown} onValueChange={(v) => v && setShown(v)}
              className="glass-bar rounded-lg" aria-label="Policy shown on the map">
              <ToggleGroupItem value={HERO}>Piggyback</ToggleGroupItem>
              <ToggleGroupItem value={BASE}>Next service</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <div className="absolute bottom-3 left-3 rounded-lg glass-bar px-3 py-1.5 text-xs text-muted-foreground ring-1 ring-border/60">
            {view.active.length} recoveries in flight · {strandedSoFar} parcels stranded so far
          </div>
        </section>
      </div>

      <aside className="flex min-w-0 flex-col gap-4">
        <section className="surface p-4" aria-label="Both policies right now">
          <h2 className="mb-3 text-sm font-semibold">At {dayTime(startAt, t)}</h2>
          <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1.5 text-sm">
            <span />
            <span className="text-right text-xs font-medium text-s1">Piggyback</span>
            <span className="text-right text-xs font-medium text-muted-foreground">Next service</span>
            {([
              ["Waiting for a decision", (s: ReturnType<typeof stateAt>) => String(s.open)],
              ["Bookings made", (s: ReturnType<typeof stateAt>) => String(s.booked)],
              ["Bookings that broke", (s: ReturnType<typeof stateAt>) => String(s.broke)],
              ["Money spent", (s: ReturnType<typeof stateAt>) => inr(s.spent)],
            ] as const).map(([label, f]) => (
              <Row key={label} label={label} a={f(now[HERO])} b={f(now[BASE])} />
            ))}
          </div>
        </section>

        <section className="surface p-4" aria-label="Open parcels over the shift">
          <h2 className="text-sm font-semibold">Parcels waiting for a decision</h2>
          <p className="mb-2 text-xs text-muted-foreground">Each hour, before planning</p>
          <OpenChart data={data} t={t} />
        </section>

        <section className="surface p-4" aria-label="End of shift">
          <h2 className="mb-2 text-sm font-semibold">End of shift</h2>
          <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1.5 text-sm">
            <span />
            <span className="text-right text-xs font-medium text-s1">Piggyback</span>
            <span className="text-right text-xs font-medium text-muted-foreground">Next service</span>
            <Row label="Stranded parcels on time" a={`${(100 * hero.kpis.on_time_rate).toFixed(1)}%`} b={`${(100 * base.kpis.on_time_rate).toFixed(1)}%`} better={hero.kpis.on_time_rate >= base.kpis.on_time_rate ? "a" : "b"} />
            <Row label="Bookings that broke" a={String(hero.kpis.broken_bookings)} b={String(base.kpis.broken_bookings)} better={hero.kpis.broken_bookings <= base.kpis.broken_bookings ? "a" : "b"} />
            <Row label="Realized penalty" a={lakh(hero.kpis.realized_penalty_inr)} b={lakh(base.kpis.realized_penalty_inr)} better={hero.kpis.realized_penalty_inr <= base.kpis.realized_penalty_inr ? "a" : "b"} />
          </div>
          {data.published && (
            <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">
              One day of {data.published.shifts} in the shift benchmark. Across all of them Piggyback's penalty is{" "}
              <span className="font-medium text-foreground tabular-nums">
                {Math.abs(data.published.penalty_pct).toFixed(1)}% {data.published.penalty_pct <= 0 ? "lower" : "higher"}
              </span>{" "}
              (±{data.published.half_width.toFixed(1)}), better on {data.published.wins} of {data.published.shifts}.
            </p>
          )}
          <p className="mt-3 flex items-start gap-1.5 text-xs text-muted-foreground">
            <FlaskConical className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            Lab only: the simulator's hidden truth decides which bookings really break. Penalty = money + ₹250 × priority × hours late.
          </p>
        </section>

        <section className="surface p-4" aria-label="Latest events">
          <h2 className="mb-2 text-sm font-semibold">Latest · {SHORT[shown]}</h2>
          {feed.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing yet. Press play.</p>
          ) : (
            <ul className="space-y-1.5 text-xs">
              {feed.map((e, i) => (
                <li key={`${e.t}-${e.shipment_id}-${e.event}-${i}`} className="flex gap-2">
                  <span className="w-12 shrink-0 text-muted-foreground tabular-nums">{dayTime(startAt, e.t).split(", ").at(-1)}</span>
                  <span className={cn("min-w-0", e.event === "broke" && "text-serious")}>
                    <span className="font-medium">{e.shipment_id}</span>{" "}
                    {e.event === "broke" ? `broke at ${e.hub}: ${e.reason}` : `${e.event} · ${MODE_LABEL[e.mode!].toLowerCase()} ${(e.route ?? [e.from_hub]).join(" → ")}`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </aside>
    </div>
  )
}

function Row({ label, a, b, better }: { label: string; a: string; b: string; better?: "a" | "b" }) {
  return (
    <>
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("text-right tabular-nums", better === "a" && "font-semibold text-foreground")}>{a}</span>
      <span className={cn("text-right tabular-nums text-muted-foreground", better === "b" && "font-semibold text-foreground")}>{b}</span>
    </>
  )
}

/** Open parcels per hour for both policies, with a cursor at `t`. */
function OpenChart({ data, t }: { data: ShiftReplay; t: number }) {
  const W = 340, H = 120, P = { l: 28, r: 8, t: 8, b: 18 }
  const { start_h: s, end_h: e } = data.config
  const series = [HERO, BASE].map((p) => data.policies[p].timeline.map((r) => [r.t, r.open] as const))
  const max = Math.max(1, ...series.flat().map(([, v]) => v))
  const x = (h: number) => P.l + ((h - s) / (e - s)) * (W - P.l - P.r)
  const y = (v: number) => P.t + (1 - v / max) * (H - P.t - P.b)
  const path = (pts: readonly (readonly [number, number])[]) => pts.map(([h, v], i) => `${i ? "L" : "M"}${x(h).toFixed(1)},${y(v).toFixed(1)}`).join(" ")
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Parcels waiting for a decision each hour, Piggyback versus next service">
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line x1={P.l} x2={W - P.r} y1={y(max * f)} y2={y(max * f)} stroke="var(--border)" />
          <text x={P.l - 4} y={y(max * f) + 3} textAnchor="end" fontSize="9" fill="var(--muted-foreground)">{Math.round(max * f)}</text>
        </g>
      ))}
      <path d={path(series[1])} fill="none" stroke="var(--muted-foreground)" strokeWidth="1.5" strokeDasharray="3 2" />
      <path d={path(series[0])} fill="none" stroke="var(--s1)" strokeWidth="2" />
      <line x1={x(t)} x2={x(t)} y1={P.t} y2={H - P.b} stroke="var(--foreground)" strokeOpacity="0.5" />
      <text x={P.l} y={H - 4} fontSize="9" fill="var(--muted-foreground)">start</text>
      <text x={W - P.r} y={H - 4} fontSize="9" textAnchor="end" fill="var(--muted-foreground)">end</text>
    </svg>
  )
}
