import { AlertTriangle, Check, CircleCheck, Clock, PackageX, Truck, Undo2, X } from "lucide-react"
import { useEffect, useMemo } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { Skeleton } from "@/components/ui/skeleton"
import { ApiError, type Detection, type RecoveryOption } from "@/lib/api"
import { dayTime, hoursText, inr, KIND_INFO, MODE_LABEL, relative, shortTime, words } from "@/lib/format"
import { READ_ONLY_HINT, useAuth } from "@/lib/auth"
import { useDecide, useOptions, useShipment } from "@/lib/queries"
import { useScenario } from "@/lib/scenario"
import { cn } from "@/lib/utils"

interface Props {
  detection: Detection
  startAt: string
  nowH: number
  planId?: string
  planChoice?: RecoveryOption
  selected: RecoveryOption | null
  onSelect: (o: RecoveryOption | null) => void
  onDecided: () => void
}

const sameOption = (a: RecoveryOption, b: RecoveryOption) => a.mode === b.mode && a.legs.join() === b.legs.join()

export function RecoveryCard({ detection, startAt, nowH, planId, planChoice, selected, onSelect, onDecided }: Props) {
  const { sid, grace } = useScenario()
  const id = detection.shipment_id
  const detail = useShipment(sid, id, grace)
  const decided = detail.data?.decision ?? null
  const options = useOptions(sid, id, grace, !decided)
  const decide = useDecide(sid, grace)
  const { canEdit } = useAuth()

  // The plan's pick first, then the live alternatives it didn't pick.
  const choices = useMemo(() => {
    const live = options.data ?? []
    const rec = planChoice ?? [...live].sort((a, b) => a.penalty_inr - b.penalty_inr)[0]
    if (!rec) return []
    return [{ option: rec, fromPlan: Boolean(planChoice) }, ...live.filter((o) => !sameOption(o, rec)).map((option) => ({ option, fromPlan: false }))]
  }, [options.data, planChoice])

  // Default the selection to the recommendation whenever the shipment changes.
  useEffect(() => {
    if (choices.length && (!selected || selected.shipment_id !== id)) onSelect(choices[0].option)
  }, [choices, selected, id, onSelect])

  const current = choices.find((c) => selected && sameOption(c.option, selected)) ?? choices[0]

  const act = (action: "ACCEPT" | "DISMISS" | "REVOKE") => {
    if (action === "ACCEPT" && (!current || current.option.mode === "NONE")) {
      toast.info("Pick a recovery to accept, or dismiss the flag.")
      return
    }
    const body =
      action === "ACCEPT"
        ? current!.fromPlan && planId
          ? { shipment_id: id, action, plan_id: planId }
          : { shipment_id: id, action, option_id: current!.option.option_id }
        : { shipment_id: id, action }
    decide.mutate(body, {
      onSuccess: () => {
        if (action === "ACCEPT") toast.success(`${id}: ${MODE_LABEL[current!.option.mode].toLowerCase()} booked`, { description: current!.option.note })
        else if (action === "DISMISS") toast(`${id} dismissed`, { description: "Marked as a false alarm." })
        else toast(`${id}: decision revoked`, { description: "Back in the queue." })
        if (action !== "REVOKE") onDecided()
      },
      onError: (e) => {
        if (e instanceof ApiError && e.status === 409) {
          toast.warning("That space was just taken", { description: "Options are refreshed with what's still free. Pick again." })
          options.refetch()
        } else toast.error("Couldn't save the decision", { description: e.message })
      },
    })
  }

  // A accept, D dismiss, 1-4 pick an option (ignored while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return
      if (e.metaKey || e.ctrlKey || e.altKey || decide.isPending || !canEdit) return
      if (e.key === "a" && !decided) act("ACCEPT")
      else if (e.key === "d" && !decided) act("DISMISS")
      else if (/^[1-9]$/.test(e.key) && choices[Number(e.key) - 1]) onSelect(choices[Number(e.key) - 1].option)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })

  const s = detail.data?.shipment
  const slack = detection.deadline_h - nowH

  return (
    <section aria-label={`Recovery for ${id}`} className="flex min-h-0 flex-col gap-4 surface p-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">
            {id}{" "}
            <span className="font-normal text-muted-foreground">
              {s ? `${s.origin_hub} → ${s.dest_hub}` : ""}
            </span>
          </h2>
          <p className="text-sm text-muted-foreground">
            {words(detection.priority)}
            {s ? ` · ${s.weight_kg} kg` : ""} · due {dayTime(startAt, detection.deadline_h)}{" "}
            <span className={cn("font-medium", slack < 12 ? "text-critical" : "text-foreground")}>({relative(slack)})</span>
          </p>
        </div>
        <Badge variant="secondary" className="gap-1">
          {words(detection.kind)}
          <span className="text-muted-foreground">· {Math.round(detection.confidence * 100)}% sure</span>
        </Badge>
      </header>

      <div className="border-l-2 pl-3 text-sm">
        <p className="text-muted-foreground">{KIND_INFO[detection.kind] ?? ""}</p>
        <p>
          Probably at <span className="font-medium">{detection.suspected_hub}</span> · {detection.evidence}
        </p>
        {detail.data && (
          <ol className="mt-2 space-y-0.5 text-xs text-muted-foreground">
            {detail.data.scans.slice(-4).map((e) => (
              <li key={e.scan_id} className="tabular-nums">
                {shortTime(startAt, e.ts_h, nowH)} · {words(e.event_type)} at {e.hub_id}
                {e.recorded_h - e.ts_h > 0.5 && <span> · uploaded {hoursText(e.recorded_h - e.ts_h)} late</span>}
              </li>
            ))}
          </ol>
        )}
      </div>

      {decided ? (
        <DecidedBanner decision={decided} startAt={startAt} nowH={nowH} onRevoke={() => act("REVOKE")} busy={decide.isPending || !canEdit} />
      ) : (
        <>
          <div className="space-y-2">
            {options.isLoading && !planChoice ? (
              <>
                <Skeleton className="h-16" />
                <Skeleton className="h-12" />
              </>
            ) : choices.length === 0 ? (
              <p className="text-sm text-muted-foreground">No recovery options for this parcel.</p>
            ) : (
              choices.map((c, i) => (
                <OptionRow
                  key={c.option.option_id + i}
                  index={i}
                  option={c.option}
                  recommended={i === 0}
                  fromPlan={c.fromPlan}
                  selected={current === c}
                  deadline={detection.deadline_h}
                  startAt={startAt}
                  nowH={nowH}
                  onSelect={() => onSelect(c.option)}
                />
              ))
            )}
          </div>

          {choices.length > 1 && <Why choices={choices.map((c) => c.option)} />}

          <div className="mt-auto flex flex-wrap items-center gap-2 border-t pt-4">
            <Button onClick={() => act("ACCEPT")} disabled={decide.isPending || !current || !canEdit}>
              <Check className="size-4" aria-hidden />
              {current && current !== choices[0] ? "Accept this option" : "Accept recommendation"}
              <Kbd className="ml-1">A</Kbd>
            </Button>
            <Button variant="outline" onClick={() => act("DISMISS")} disabled={decide.isPending || !canEdit}>
              <X className="size-4" aria-hidden />
              Dismiss flag
              <Kbd className="ml-1">D</Kbd>
            </Button>
            {!canEdit && <span className="text-xs text-muted-foreground">{READ_ONLY_HINT}</span>}
            <span className="ml-auto text-xs text-muted-foreground">
              <Kbd>1</Kbd>–<Kbd>{Math.min(9, choices.length)}</Kbd> pick an option
            </span>
          </div>
        </>
      )}
    </section>
  )
}

/** Why the first option wins: lowest money spent + value of lateness. */
function Why({ choices }: { choices: RecoveryOption[] }) {
  const [rec, ...rest] = choices
  const lateValue = (o: RecoveryOption) => o.penalty_inr - o.cost_inr
  // Contrast with the alternative an operator is most tempted by: the fastest one.
  const rival = [...rest].filter((o) => o.mode !== "NONE").sort((a, b) => a.lateness_h - b.lateness_h)[0]
  if (!rival) return null
  return (
    <p className="text-xs text-muted-foreground">
      Recommended for the lowest total of money spent plus lateness (₹250 per priority-weighted hour late):{" "}
      <span className="font-medium text-foreground tabular-nums">{inr(rec.penalty_inr)}</span> ({inr(rec.cost_inr)} + {inr(lateValue(rec))} lateness) vs{" "}
      <span className="tabular-nums">{inr(rival.penalty_inr)}</span> for {MODE_LABEL[rival.mode].toLowerCase()}
      {rival.lateness_h <= 0.01 ? " on time" : ""}.
    </p>
  )
}

function OptionRow({
  index, option: o, recommended, fromPlan, selected, deadline, startAt, nowH, onSelect,
}: {
  index: number
  option: RecoveryOption
  recommended: boolean
  fromPlan: boolean
  selected: boolean
  deadline: number
  startAt: string
  nowH: number
  onSelect: () => void
}) {
  const late = o.lateness_h > 0.01
  const Icon = o.mode === "DEDICATED" ? Truck : o.mode === "NONE" ? PackageX : CircleCheck
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 rounded-xl border border-border/70 px-3.5 py-3 text-left transition-all duration-150 hover:border-border hover:bg-foreground/[0.02]",
        selected && "border-s1/60 bg-s1/[0.05] shadow-[inset_0_1px_0_rgb(255_255_255/0.5),0_0_0_1px_color-mix(in_oklab,var(--s1)_35%,transparent)]",
      )}
    >
      <Icon className={cn("size-5", o.mode === "NONE" ? "text-muted-foreground" : o.mode === "DEDICATED" ? "text-s2" : "text-s1")} aria-hidden />
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
          {MODE_LABEL[o.mode]}
          {recommended && (
            <Badge variant="outline" className="text-[11px]">
              {fromPlan ? "Plan's pick" : "Best now"}
            </Badge>
          )}
          <span className="text-xs font-normal text-muted-foreground">
            <Kbd>{index + 1}</Kbd>
          </span>
        </div>
        <div className="truncate text-xs text-muted-foreground">
          {o.mode === "NONE" ? "Counts as 48 h late if left" : o.note}
          {o.depart_h != null && o.mode !== "NONE" ? ` · leaves ${shortTime(startAt, o.depart_h, nowH)}` : ""}
        </div>
        {o.load_after_pct != null && selected && <LoadBar pct={o.load_after_pct} />}
      </div>
      <div className="text-right text-sm tabular-nums">
        {o.delivered_h != null ? (
          <div className={cn("flex items-center justify-end gap-1 font-medium", late ? "text-warn" : "text-good")}>
            {late ? <AlertTriangle className="size-3.5" aria-hidden /> : <Clock className="size-3.5" aria-hidden />}
            {late ? `${hoursText(o.lateness_h)} late` : `on time, ${hoursText(deadline - o.delivered_h)} spare`}
          </div>
        ) : (
          <div className="font-medium text-critical">not recovered</div>
        )}
        <div className="text-xs text-muted-foreground">{o.cost_inr ? inr(o.cost_inr) : "no cost"}</div>
      </div>
    </button>
  )
}

function LoadBar({ pct }: { pct: number }) {
  const full = pct >= 90
  return (
    <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
      <div className="h-1.5 w-32 overflow-hidden rounded-full bg-muted" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Fullest truck after booking">
        <div className={cn("h-full rounded-full", full ? "bg-critical" : "bg-s1")} style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      fullest truck {Math.round(pct)}% loaded after this
      {full && <AlertTriangle className="size-3.5 text-critical" aria-label="nearly full" />}
    </div>
  )
}

function DecidedBanner({
  decision, startAt, nowH, onRevoke, busy,
}: {
  decision: NonNullable<ReturnType<typeof useShipment>["data"]>["decision"]
  startAt: string
  nowH: number
  onRevoke: () => void
  busy: boolean
}) {
  if (!decision) return null
  const o = decision.option
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-good/40 bg-good/5 p-3 text-sm">
      <CircleCheck className="size-5 text-good" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{decision.action === "ACCEPT" ? `Accepted: ${MODE_LABEL[o!.mode].toLowerCase()}` : "Dismissed as a false alarm"}</div>
        <div className="text-xs text-muted-foreground">
          {o ? `${o.note}${o.delivered_h != null ? ` · arrives ${shortTime(startAt, o.delivered_h, nowH)}` : ""} · ` : ""}
          by {decision.decided_by}
        </div>
      </div>
      <Button variant="outline" size="sm" onClick={onRevoke} disabled={busy}>
        <Undo2 className="size-4" aria-hidden />
        Revoke
      </Button>
    </div>
  )
}
