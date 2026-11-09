import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api, type Action } from "@/lib/api"

export const keys = {
  scenarios: ["scenarios"] as const,
  scenario: (sid: string) => ["scenario", sid] as const,
  network: (sid: string) => ["network", sid] as const,
  disruptions: (sid: string) => ["disruptions", sid] as const,
  detections: (sid: string, grace: number) => ["detections", sid, grace] as const,
  shipment: (sid: string, id: string, grace: number) => ["shipment", sid, id, grace] as const,
  options: (sid: string, id: string, grace: number) => ["options", sid, id, grace] as const,
  hub: (sid: string, hub: string, grace: number) => ["hub", sid, hub, grace] as const,
  plans: (sid: string) => ["plans", sid] as const,
  plan: (sid: string, planId: string) => ["plan", sid, planId] as const,
  decisions: (sid: string, history: boolean) => ["decisions", sid, history] as const,
  benchmark: ["benchmark"] as const,
}

const on = (sid: string | null | undefined) => Boolean(sid)

export const useScenarios = () => useQuery({ queryKey: keys.scenarios, queryFn: api.scenarios })

export const useScenarioSummary = (sid: string | null) =>
  useQuery({ queryKey: keys.scenario(sid ?? ""), queryFn: () => api.scenario(sid!), enabled: on(sid) })

export const useNetwork = (sid: string | null) =>
  useQuery({ queryKey: keys.network(sid ?? ""), queryFn: () => api.network(sid!), enabled: on(sid), staleTime: Infinity })

export const useDisruptions = (sid: string | null) =>
  useQuery({ queryKey: keys.disruptions(sid ?? ""), queryFn: () => api.disruptions(sid!), enabled: on(sid), staleTime: Infinity })

export const useDetections = (sid: string | null, grace: number) =>
  useQuery({ queryKey: keys.detections(sid ?? "", grace), queryFn: () => api.detections(sid!, grace), enabled: on(sid) })

export const useShipment = (sid: string | null, id: string | null, grace: number) =>
  useQuery({
    queryKey: keys.shipment(sid ?? "", id ?? "", grace),
    queryFn: () => api.shipment(sid!, id!, grace),
    enabled: on(sid) && Boolean(id),
  })

export const useOptions = (sid: string | null, id: string | null, grace: number, enabled = true) =>
  useQuery({
    queryKey: keys.options(sid ?? "", id ?? "", grace),
    queryFn: () => api.options(sid!, id!, grace),
    enabled: enabled && on(sid) && Boolean(id),
  })

export const useHub = (sid: string | null, hub: string | null, grace: number) =>
  useQuery({
    queryKey: keys.hub(sid ?? "", hub ?? "", grace),
    queryFn: () => api.hub(sid!, hub!, grace),
    enabled: on(sid) && Boolean(hub),
  })

export const usePlans = (sid: string | null) =>
  useQuery({ queryKey: keys.plans(sid ?? ""), queryFn: () => api.plans(sid!), enabled: on(sid) })

export const usePlan = (sid: string | null, planId: string | null | undefined) =>
  useQuery({
    queryKey: keys.plan(sid ?? "", planId ?? ""),
    queryFn: () => api.plan(sid!, planId!),
    enabled: on(sid) && Boolean(planId),
    staleTime: Infinity, // a saved plan never changes
  })

/** The most recent plan, which the queue's recommendations come from. */
export function useLatestPlan(sid: string | null) {
  const plans = usePlans(sid)
  const latest = plans.data?.at(-1)
  return { plans, plan: usePlan(sid, latest?.plan_id) }
}

export const useDecisions = (sid: string | null, history = false) =>
  useQuery({ queryKey: keys.decisions(sid ?? "", history), queryFn: () => api.decisions(sid!, history), enabled: on(sid) })

export const useBenchmark = () => useQuery({ queryKey: keys.benchmark, queryFn: api.benchmark, staleTime: Infinity })

/** Anything that depends on decisions in force or on free capacity. */
function invalidateOperational(qc: ReturnType<typeof useQueryClient>, sid: string) {
  for (const k of ["detections", "shipment", "options", "hub", "decisions"]) {
    qc.invalidateQueries({ queryKey: [k, sid] })
  }
}

export function useDecide(sid: string | null, grace: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { shipment_id: string; action: Action; plan_id?: string; option_id?: string; note?: string }) =>
      api.decide(sid!, { ...v, grace_h: grace }),
    onSettled: () => sid && invalidateOperational(qc, sid),
  })
}

export function useCreatePlan(sid: string | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { solver: string; grace_h: number; include_at_risk?: boolean }) => api.createPlan(sid!, v),
    onSuccess: () => sid && qc.invalidateQueries({ queryKey: keys.plans(sid) }),
  })
}

export function useCreateScenario() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: api.createScenario,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.scenarios }),
  })
}
