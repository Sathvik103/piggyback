import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import { mine } from "@/lib/mine"
import { invalidateOperational, keys } from "@/lib/queries"

export type LiveStatus = "connecting" | "live" | "offline"

type DecisionEvent = { type: "decision"; scenario_id: string; shipment_id: string; action: "ACCEPT" | "DISMISS" | "REVOKE"; decision_id: number; decided_by: string }
type PlanEvent = { type: "plan"; scenario_id: string; plan_id: string; solver: string }

const VERB = { ACCEPT: "accepted", DISMISS: "dismissed", REVOKE: "reopened" } as const

// The stream can deliver our own change before our POST's response arrives, so wait
// a moment before deciding whether a change came from another session.
const ECHO_GRACE_MS = 1500

/**
 * Subscribe to the scenario's event stream (`/scenarios/{sid}/events`). Other
 * sessions' decisions and plans refresh the queue, map and plan views as they
 * happen; after any gap (reconnect or `resync`) everything for the scenario is
 * refetched, since events sent meanwhile are lost.
 */
export function useLiveUpdates(sid: string | null): LiveStatus {
  const qc = useQueryClient()
  const [status, setStatus] = useState<LiveStatus>("connecting")

  useEffect(() => {
    if (!sid) return
    const src = new EventSource(`/scenarios/${encodeURIComponent(sid)}/events`)
    let dropped = false
    const resync = () => qc.invalidateQueries({ predicate: (q) => q.queryKey[1] === sid })

    src.addEventListener("ready", () => {
      setStatus("live")
      if (dropped) resync()
      dropped = false
    })
    src.onerror = () => {
      dropped = true
      setStatus(src.readyState === EventSource.CLOSED ? "offline" : "connecting")
    }
    src.addEventListener("resync", resync)
    src.addEventListener("decision", (e) => {
      const ev = JSON.parse((e as MessageEvent).data) as DecisionEvent
      invalidateOperational(qc, sid)
      setTimeout(() => {
        if (!mine.takeDecision(ev.decision_id)) {
          toast(`${ev.shipment_id} ${VERB[ev.action]} in another session`, { description: `by ${ev.decided_by}` })
        }
      }, ECHO_GRACE_MS)
    })
    src.addEventListener("plan", (e) => {
      const ev = JSON.parse((e as MessageEvent).data) as PlanEvent
      qc.invalidateQueries({ queryKey: keys.plans(sid) })
      setTimeout(() => {
        if (!mine.takePlan(ev.plan_id)) toast(`Plan ${ev.plan_id} created in another session`)
      }, ECHO_GRACE_MS)
    })
    return () => {
      src.close()
      setStatus("connecting")
    }
  }, [sid, qc])

  return sid ? status : "offline"
}
