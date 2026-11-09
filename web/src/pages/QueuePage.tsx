import { Inbox } from "lucide-react"
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react"

import { RecoveryCard } from "@/components/RecoveryCard"
import { Kbd } from "@/components/ui/kbd"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import type { Detection, RecoveryOption } from "@/lib/api"
import { MODE_LABEL, PRIORITY_RANK, relative, words } from "@/lib/format"
import { useDetections, useDisruptions, useLatestPlan, useNetwork, useScenarioSummary, useShipment } from "@/lib/queries"
import { useScenario } from "@/lib/scenario"
import { cn } from "@/lib/utils"

// MapLibre is big; load it after the queue is on screen.
const NetworkMap = lazy(() => import("@/components/NetworkMap").then((m) => ({ default: m.NetworkMap })))

type View = "open" | "watching" | "decided"

export function QueuePage() {
  const { sid, grace } = useScenario()
  const summary = useScenarioSummary(sid)
  const detections = useDetections(sid, grace)
  const { plan, plans } = useLatestPlan(sid)
  const net = useNetwork(sid)
  const disruptions = useDisruptions(sid)

  const [view, setView] = useState<View>("open")
  const [priority, setPriority] = useState("ALL")
  const [kind, setKind] = useState("ALL")
  const [pickedId, setSelectedId] = useState<string | null>(null)
  const [option, setOption] = useState<RecoveryOption | null>(null)

  const nowH = summary.data?.now_h ?? 0
  const items = useMemo(() => detections.data?.items ?? [], [detections.data])
  const choiceBy = useMemo(() => Object.fromEntries((plan.data?.choices ?? []).map((c) => [c.shipment_id, c])), [plan.data])

  const groups = useMemo(() => {
    const open = items.filter((d) => !d.decision && d.kind !== "AT_RISK")
    const watching = items.filter((d) => !d.decision && d.kind === "AT_RISK")
    const decided = items.filter((d) => d.decision)
    return { open, watching, decided }
  }, [items])

  const list = useMemo(() => {
    return groups[view]
      .filter((d) => (priority === "ALL" || d.priority === priority) && (kind === "ALL" || d.kind === kind))
      .sort((a, b) => a.deadline_h - b.deadline_h || PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority])
  }, [groups, view, priority, kind])

  // Keep a valid selection: the picked row if it's still listed, else the most urgent.
  const selectedId = list.some((d) => d.shipment_id === pickedId) ? pickedId : (list[0]?.shipment_id ?? null)

  const index = list.findIndex((d) => d.shipment_id === selectedId)
  const selected = index >= 0 ? list[index] : null
  const listRef = useRef<HTMLDivElement>(null)

  const move = useCallback(
    (delta: number) => {
      const next = list[Math.min(list.length - 1, Math.max(0, index + delta))]
      if (next) {
        setSelectedId(next.shipment_id)
        listRef.current?.querySelector(`[data-id="${next.shipment_id}"]`)?.scrollIntoView({ block: "nearest" })
      }
    },
    [list, index],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault()
        move(1)
      }
      if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault()
        move(-1)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [move])

  // After a decision the decided row leaves the open list; select its neighbour.
  const onDecided = useCallback(() => {
    const next = list[index + 1] ?? list[index - 1]
    setSelectedId(next ? next.shipment_id : null)
  }, [list, index])

  const kinds = useMemo(() => [...new Set(groups[view].map((d) => d.kind))].sort(), [groups, view])
  const dueSoon = groups.open.filter((d) => d.deadline_h - nowH < 12).length
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const d of groups.open) c[d.suspected_hub] = (c[d.suspected_hub] ?? 0) + 1
    return c
  }, [groups.open])
  const closed = useMemo(() => new Set((disruptions.data ?? []).filter((d) => d.kind === "HUB_CLOSURE").map((d) => d.hub_id!)), [disruptions.data])

  if (!sid) return <Empty title="No scenario yet" body="Create one in the Lab to get started." />

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 p-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Needs a decision" value={groups.open.length} loading={detections.isLoading} />
        <Kpi label="Due within 12 h" value={dueSoon} tone={dueSoon ? "critical" : undefined} loading={detections.isLoading} />
        <Kpi label="Watching (at risk)" value={groups.watching.length} hint="Late trucks; acted on once they strand" loading={detections.isLoading} />
        <Kpi label="Decided" value={groups.decided.length} loading={detections.isLoading} />
      </div>

      {!plans.isLoading && !plans.data?.length && groups.open.length > 0 && (
        <p className="rounded-lg border border-dashed px-4 py-2 text-sm text-muted-foreground">
          No plan yet, so recommendations are the cheapest live option per parcel. Press <span className="font-medium text-foreground">Plan now</span> for an
          optimised plan across all parcels and shared truck space.
        </p>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[320px_minmax(0,1fr)_340px]">
        <section aria-label="Exceptions queue" className="flex min-h-[320px] flex-col overflow-hidden rounded-xl border bg-card">
          <div className="space-y-2 border-b p-3">
            <ToggleGroup type="single" value={view} onValueChange={(v) => v && setView(v as View)} variant="outline" size="sm" className="w-full">
              <ToggleGroupItem value="open" className="flex-1">Open {groups.open.length}</ToggleGroupItem>
              <ToggleGroupItem value="watching" className="flex-1">Watching {groups.watching.length}</ToggleGroupItem>
              <ToggleGroupItem value="decided" className="flex-1">Decided {groups.decided.length}</ToggleGroupItem>
            </ToggleGroup>
            <div className="flex gap-2">
              <Select value={priority} onValueChange={setPriority}>
                <SelectTrigger size="sm" className="flex-1" aria-label="Priority filter"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">All priorities</SelectItem>
                  <SelectItem value="EXPRESS">Express</SelectItem>
                  <SelectItem value="STANDARD">Standard</SelectItem>
                  <SelectItem value="ECONOMY">Economy</SelectItem>
                </SelectContent>
              </Select>
              <Select value={kind} onValueChange={setKind}>
                <SelectTrigger size="sm" className="flex-1" aria-label="Kind filter"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL">All kinds</SelectItem>
                  {kinds.map((k) => (
                    <SelectItem key={k} value={k}>{words(k)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto" role="listbox" aria-label="Shipments">
            {detections.isLoading
              ? Array.from({ length: 8 }, (_, i) => <Skeleton key={i} className="m-3 h-11" />)
              : list.length === 0
                ? <p className="p-6 text-center text-sm text-muted-foreground">{view === "open" ? "Nothing waiting. The queue is clear." : "None here."}</p>
                : list.map((d) => (
                    <QueueRow key={d.shipment_id} d={d} nowH={nowH} choice={choiceBy[d.shipment_id]} active={d.shipment_id === selectedId} onClick={() => setSelectedId(d.shipment_id)} />
                  ))}
          </div>
          <div className="border-t px-3 py-2 text-xs text-muted-foreground">
            <Kbd>J</Kbd> <Kbd>K</Kbd> move · <Kbd>A</Kbd> accept · <Kbd>D</Kbd> dismiss
          </div>
        </section>

        {selected && summary.data ? (
          <RecoveryCard
            key={selected.shipment_id}
            detection={selected}
            startAt={summary.data.start_at}
            nowH={nowH}
            planId={plan.data?.plan_id}
            planChoice={choiceBy[selected.shipment_id]}
            selected={option}
            onSelect={setOption}
            onDecided={onDecided}
          />
        ) : (
          <Empty title={detections.isLoading ? "Loading…" : "Queue clear"} body={detections.isLoading ? "" : "Every flagged parcel has a decision."} />
        )}

        <aside aria-label="Route" className="hidden min-h-[320px] overflow-hidden rounded-xl border bg-card xl:block">
          {net.data && selected ? (
            <RouteContext shipmentId={selected.shipment_id} hub={selected.suspected_hub} option={option} geo={net.data} counts={counts} closed={closed} />
          ) : (
            <Skeleton className="h-full w-full" />
          )}
        </aside>
      </div>
    </div>
  )
}

function RouteContext({ shipmentId, hub, option, geo, counts, closed }: {
  shipmentId: string
  hub: string
  option: RecoveryOption | null
  geo: NonNullable<ReturnType<typeof useNetwork>["data"]>
  counts: Record<string, number>
  closed: Set<string>
}) {
  const { sid, grace } = useScenario()
  const detail = useShipment(sid, shipmentId, grace)
  const legs = detail.data?.booked_legs
  const booked = useMemo(() => (legs?.length ? [legs[0].from_hub, ...legs.map((l) => l.to_hub)] : null), [legs])
  const recovery = useMemo(
    () => (option && option.shipment_id === shipmentId ? { route: option.route, mode: option.mode } : null),
    [option, shipmentId],
  )
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1">
        <Suspense fallback={<Skeleton className="h-full w-full" />}>
          <NetworkMap geo={geo} compact counts={counts} selectedHub={hub} closedHubs={closed} booked={booked} recovery={recovery} />
        </Suspense>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t px-3 py-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="inline-block w-5 border-t-2 border-dotted border-muted-foreground" />Original booking</span>
        <span className="flex items-center gap-1.5"><span className="inline-block w-5 border-t-[3px] border-s1" />{option ? MODE_LABEL[option.mode] : "Recovery"}</span>
        <span className="flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-full outline-2 outline-foreground" />Parcel is here</span>
      </div>
    </div>
  )
}

function QueueRow({ d, nowH, choice, active, onClick }: { d: Detection; nowH: number; choice?: RecoveryOption; active: boolean; onClick: () => void }) {
  const slack = d.deadline_h - nowH
  return (
    <button
      type="button"
      role="option"
      aria-selected={active}
      data-id={d.shipment_id}
      onClick={onClick}
      className={cn("block w-full border-b px-3 py-2.5 text-left transition-colors hover:bg-muted/60", active && "bg-muted")}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">
          {d.shipment_id}
          {d.priority === "EXPRESS" && <span className="ml-1.5 rounded bg-s1/10 px-1 text-[10px] font-semibold uppercase tracking-wide text-s1">Express</span>}
        </span>
        <span className={cn("text-xs tabular-nums", slack < 12 ? "font-medium text-critical" : "text-muted-foreground")}>due {relative(slack)}</span>
      </div>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate">{words(d.kind)} · at {d.suspected_hub}</span>
        {d.decision ? (
          <span className="shrink-0 text-good">{words(d.decision)}ed</span>
        ) : choice ? (
          <span className={cn("shrink-0", choice.mode === "NONE" ? "text-muted-foreground" : choice.lateness_h > 0.01 ? "text-warn" : "text-good")}>
            {MODE_LABEL[choice.mode]}
            {choice.mode !== "NONE" ? (choice.lateness_h > 0.01 ? " · late" : " · on time") : ""}
          </span>
        ) : null}
      </div>
    </button>
  )
}

function Kpi({ label, value, tone, hint, loading }: { label: string; value: number; tone?: "critical"; hint?: string; loading?: boolean }) {
  return (
    <div className="rounded-xl border bg-card px-4 py-3" title={hint}>
      <div className="text-xs text-muted-foreground">{label}</div>
      {loading ? <Skeleton className="mt-1 h-7 w-12" /> : <div className={cn("text-2xl font-semibold tabular-nums", tone === "critical" && "text-critical")}>{value}</div>}
    </div>
  )
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full min-h-[240px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-8 text-center">
      <Inbox className="size-8 text-muted-foreground" aria-hidden />
      <div className="font-medium">{title}</div>
      {body && <p className="max-w-sm text-sm text-muted-foreground">{body}</p>}
    </div>
  )
}
