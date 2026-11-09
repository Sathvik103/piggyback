import { AlertTriangle, Ban, Truck, X } from "lucide-react"
import { useMemo, useState } from "react"

import { type Hop, NetworkMap } from "@/components/NetworkMap"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { hoursText, shortTime, words } from "@/lib/format"
import { useDetections, useDisruptions, useHub, useLatestPlan, useNetwork, useScenarioSummary } from "@/lib/queries"
import { useScenario } from "@/lib/scenario"
import { cn } from "@/lib/utils"

export function NetworkPage() {
  const { sid, grace } = useScenario()
  const summary = useScenarioSummary(sid)
  const net = useNetwork(sid)
  const detections = useDetections(sid, grace)
  const disruptions = useDisruptions(sid)
  const { plan } = useLatestPlan(sid)
  const [hub, setHub] = useState<string | null>(null)

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const d of detections.data?.items ?? []) if (!d.decision) c[d.suspected_hub] = (c[d.suspected_hub] ?? 0) + 1
    return c
  }, [detections.data])

  const { hops, dedicated } = useMemo(() => {
    const h: Record<string, Hop> = {}
    const ded: string[][] = []
    for (const c of plan.data?.choices ?? []) {
      if (c.mode === "PIGGYBACK") {
        for (let i = 0; i + 1 < c.route.length; i++) {
          const k = `${c.route[i]}>${c.route[i + 1]}`
          h[k] = h[k] ?? { a: c.route[i], b: c.route[i + 1], n: 0 }
          h[k].n++
        }
      } else if (c.mode === "DEDICATED") ded.push(c.route)
    }
    return { hops: Object.values(h), dedicated: ded }
  }, [plan.data])

  const closed = useMemo(() => new Set((disruptions.data ?? []).filter((d) => d.kind === "HUB_CLOSURE").map((d) => d.hub_id!)), [disruptions.data])
  const delayed = useMemo(
    () =>
      (disruptions.data ?? [])
        .filter((d) => d.kind === "LANE_DELAY")
        .map((d) => {
          const [from, to] = d.lane_id!.split("-")
          return { from, to, label: `+${hoursText(d.delay_h ?? 0)}` }
        }),
    [disruptions.data],
  )

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_380px]">
      <section className="relative min-h-[420px] overflow-hidden rounded-xl border bg-card">
        {net.data ? (
          <NetworkMap geo={net.data} counts={counts} selectedHub={hub} closedHubs={closed} hops={hops} dedicated={dedicated} delayedLanes={delayed} onHubClick={setHub} />
        ) : (
          <Skeleton className="h-full w-full" />
        )}
        <div className="absolute bottom-3 left-3 grid gap-1 rounded-lg border bg-background/95 px-3 py-2 text-xs text-muted-foreground shadow-sm">
          <span className="flex items-center gap-2"><span className="inline-block size-2.5 rounded-full bg-s1" />Hub · size = parcels needing a decision</span>
          <span className="flex items-center gap-2"><span className="inline-block w-5 border-t-[3px] border-s1" />Piggyback hop{plan.data ? ` (plan ${plan.data.plan_id})` : ""} · width = parcels</span>
          <span className="flex items-center gap-2"><span className="inline-block w-5 border-t-2 border-dashed border-s2" />Dedicated truck</span>
          <span className="flex items-center gap-2"><AlertTriangle className="size-3 text-serious" aria-hidden />Lane delay (dashed)</span>
          <span className="flex items-center gap-2"><Ban className="size-3 text-critical" aria-hidden />Hub closed (ringed)</span>
        </div>
      </section>

      <aside className="flex min-h-0 flex-col gap-4 overflow-y-auto">
        {hub ? (
          <HubPanel hub={hub} onClose={() => setHub(null)} startAt={summary.data?.start_at} nowH={summary.data?.now_h ?? 0} />
        ) : (
          <div className="rounded-xl border bg-card p-4 text-sm text-muted-foreground">Select a hub on the map to see what's stuck there and which trucks leave soon with room.</div>
        )}
        <section className="rounded-xl border bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold">Known disruptions</h2>
          <ul className="space-y-2 text-sm">
            {(disruptions.data ?? []).map((d) => (
              <li key={d.disruption_id} className="flex items-start gap-2">
                {d.kind === "LANE_DELAY" ? <AlertTriangle className="mt-0.5 size-4 shrink-0 text-serious" aria-hidden /> : d.kind === "HUB_CLOSURE" ? <Ban className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden /> : <Truck className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden />}
                <div>
                  <div className="font-medium">
                    {words(d.kind)} · {d.hub_id ?? d.lane_id?.replace("-", " → ") ?? d.trip_id}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {summary.data && `${shortTime(summary.data.start_at, d.start_h, summary.data.now_h)}–${shortTime(summary.data.start_at, d.end_h, summary.data.now_h)}`}
                    {d.delay_h ? ` · +${hoursText(d.delay_h)}` : d.kind === "HUB_CLOSURE" ? " · no loading or unloading" : " · trip stops running"}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      </aside>
    </div>
  )
}

function HubPanel({ hub, onClose, startAt, nowH }: { hub: string; onClose: () => void; startAt?: string; nowH: number }) {
  const { sid, grace } = useScenario()
  const q = useHub(sid, hub, grace)
  const d = q.data
  const open = d?.detections.filter((x) => !x.decision) ?? []
  return (
    <section className="rounded-xl border bg-card p-4" aria-label={`Hub ${hub}`}>
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">{d ? `${d.hub.name} (${hub})` : hub}</h2>
          {d && <p className="text-xs text-muted-foreground">{d.hub.state} · sorts {d.hub.sort_capacity_per_h.toLocaleString("en-IN")} parcels/h</p>}
        </div>
        <Button variant="ghost" size="icon" aria-label="Close hub panel" onClick={onClose}><X className="size-4" /></Button>
      </div>
      {!d ? (
        <Skeleton className="h-40" />
      ) : (
        <>
          <div className="mb-3 flex flex-wrap gap-1.5">
            <Badge variant="secondary">{open.length} needing a decision</Badge>
            <Badge variant="outline">{d.detections.length - open.length} decided</Badge>
          </div>
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">Departures in the next 24 h</h3>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>To</TableHead>
                <TableHead>Leaves</TableHead>
                <TableHead className="text-right">Free kg</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {d.departures.length === 0 && (
                <TableRow><TableCell colSpan={3} className="text-muted-foreground">None</TableCell></TableRow>
              )}
              {[...d.departures].sort((a, b) => a.dep - b.dep).map((x) => {
                const share = x.spare_kg > 0 ? x.free_kg / x.spare_kg : 0
                return (
                  <TableRow key={x.leg_id}>
                    <TableCell className="font-medium">{x.to_hub}</TableCell>
                    <TableCell className="tabular-nums">{startAt ? shortTime(startAt, x.dep, nowH) : x.dep.toFixed(1)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      <span className={cn(share < 0.25 && "text-critical")}>{Math.round(x.free_kg).toLocaleString("en-IN")}</span>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </>
      )}
    </section>
  )
}
