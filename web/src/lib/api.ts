// Typed client for the Piggyback API (FastAPI). Shapes mirror api/app.py.

export type Priority = "EXPRESS" | "STANDARD" | "ECONOMY"
export type Mode = "PIGGYBACK" | "DEDICATED" | "LOCAL" | "NONE"
export type Action = "ACCEPT" | "DISMISS" | "REVOKE"

export interface ScenarioSummary {
  scenario_id: string
  seed: number
  start_at: string
  now_h: number
  now_at: string
  hubs: number
  lanes: number
  trips: number
  shipments: number
  stranded: number
  delayed_trips: number
  stranded_by_type: Record<string, number>
  disruptions: Record<string, number>
  mean_spare_kg_fraction: number
}

export interface Detection {
  shipment_id: string
  kind: string
  suspected_hub: string
  priority: Priority
  deadline_h: number
  last_scan_h: number
  expected_h: number | null
  overdue_h: number
  confidence: number
  cluster_id: string | null
  evidence: string
  decision: Action | null
}

export interface Detections {
  now_h: number
  grace_h: number
  total: number
  stranded: number
  open: number
  by_kind: Record<string, number>
  by_hub: Record<string, number>
  items: Detection[]
}

export interface RecoveryOption {
  option_id: string
  shipment_id: string
  mode: Mode
  from_hub: string
  ready_h: number
  legs: string[]
  capacity_legs: string[]
  depart_h: number | null
  delivered_h: number | null
  lateness_h: number
  cost_inr: number
  penalty_inr: number
  weight_kg: number
  volume_m3: number
  note: string
  route: string[]
  load_after_pct: number | null
}

export interface PlanSummary {
  plan_id: string
  solver: "cpsat" | "greedy" | "none"
  grace_h: number
  status: string
  objective_inr: number
  solve_s: number
  options_considered: number
  created_at: string
  shipments: number
  cost_inr: number
  by_mode: Partial<Record<Mode, number>>
}

export interface Plan extends PlanSummary {
  choices: RecoveryOption[]
}

export interface Decision {
  decision_id: number
  shipment_id: string
  action: Action
  plan_no: number | null
  option: RecoveryOption | null
  note: string
  decided_by: string
  decided_at: string
}

export interface Leg {
  leg_id: string
  trip_id: string
  seq: number
  from_hub: string
  to_hub: string
  departure_h: number
  arrival_h: number
}

export interface Scan {
  scan_id: string
  shipment_id: string
  ts_h: number
  recorded_h: number
  hub_id: string
  event_type: string
  leg_id: string | null
  note: string | null
}

export interface Shipment {
  shipment_id: string
  origin_hub: string
  dest_hub: string
  priority: Priority
  weight_kg: number
  volume_m3: number
  created_h: number
  deadline_h: number
  planned_legs: string
  planned_arrival_h: number
}

export interface ShipmentDetail {
  shipment: Shipment
  booked_legs: Leg[]
  scans: Scan[]
  detection: Detection | null
  decision: Decision | null
  plans: { plan_id: string; solver: string; choice: RecoveryOption }[]
}

export interface Hub {
  hub_id: string
  name: string
  state: string
  lat: number
  lon: number
  sort_capacity_per_h: number
}

export interface Disruption {
  disruption_id: string
  kind: "TRIP_BREAKDOWN" | "HUB_CLOSURE" | "LANE_DELAY"
  start_h: number
  end_h: number
  trip_id: string | null
  hub_id: string | null
  lane_id: string | null
  delay_h: number | null
}

export interface NetworkGeo {
  type: "FeatureCollection"
  features: {
    type: "Feature"
    id: string
    geometry: { type: "Point" | "LineString"; coordinates: number[] | number[][] }
    properties: { kind: "hub" | "lane" } & Record<string, unknown>
  }[]
}

export interface Departure {
  leg_id: string
  trip_id: string
  from_hub: string
  to_hub: string
  dep: number
  arr: number
  spare_kg: number
  free_kg: number
  free_m3: number
}

export interface HubDetail {
  hub: Hub
  now_h: number
  detections: Detection[]
  departures: Departure[]
}

export interface ReplayKpis {
  stranded: number
  on_time: number
  late: number
  failed: number
  unrecovered: number
  on_time_rate: number
  cost_inr: number
  realized_penalty_inr: number
  unnecessary: number
}

export interface Stat {
  mean: number | null
  ci95: number | null
  significant?: boolean
}

export interface Paired {
  n: number
  wins: number
  ontime_pp: Stat
  penalty_pct: Stat
  cost_lakh: Stat
}

export interface BenchmarkSummary {
  hero: string
  available: boolean
  meta?: { scenarios: number; seeds: [number, number]; tightness: number[]; anomaly_rate: number[] }
  policies?: ({ name: string; label: string; family: string } & Record<string, Stat | string>)[]
  versus?: Record<string, Paired>
  by_tightness?: Record<string, Record<string, Paired>>
  robustness: {
    variant: string
    note: string
    hero_on_time_pct: Stat
    failed_pct: { hero: number; next_service: number }
    vs_next_service: Paired
    vs_express_truck: Paired
    ablations: Record<string, Paired>
  }[]
}

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, message: string, body: unknown) {
    super(message)
    this.status = status
    this.body = body
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  })
  const text = await res.text()
  const body = text ? JSON.parse(text) : null
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : res.statusText
    throw new ApiError(res.status, detail, body)
  }
  return body as T
}

const q = (params: Record<string, string | number | boolean | undefined>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined) s.set(k, String(v))
  const str = s.toString()
  return str ? `?${str}` : ""
}

const sc = (sid: string) => `/scenarios/${encodeURIComponent(sid)}`

export const api = {
  scenarios: () => request<ScenarioSummary[]>("/scenarios"),
  scenario: (sid: string) => request<ScenarioSummary>(sc(sid)),
  createScenario: (body: Record<string, unknown>) =>
    request<ScenarioSummary>("/scenarios", { method: "POST", body: JSON.stringify(body) }),
  network: (sid: string) => request<NetworkGeo>(`${sc(sid)}/network`),
  disruptions: (sid: string) => request<Disruption[]>(`${sc(sid)}/disruptions`),
  detections: (sid: string, grace_h: number) =>
    request<Detections>(`${sc(sid)}/detections${q({ grace_h, limit: 5000 })}`),
  shipment: (sid: string, id: string, grace_h: number) =>
    request<ShipmentDetail>(`${sc(sid)}/shipments/${encodeURIComponent(id)}${q({ grace_h })}`),
  options: (sid: string, id: string, grace_h: number) =>
    request<RecoveryOption[]>(`${sc(sid)}/shipments/${encodeURIComponent(id)}/options${q({ grace_h })}`),
  hub: (sid: string, hub: string, grace_h: number) =>
    request<HubDetail>(`${sc(sid)}/hubs/${encodeURIComponent(hub)}${q({ grace_h, window_h: 24 })}`),
  plans: (sid: string) => request<PlanSummary[]>(`${sc(sid)}/plans`),
  plan: (sid: string, planId: string) => request<Plan>(`${sc(sid)}/plans/${encodeURIComponent(planId)}`),
  createPlan: (sid: string, body: { solver: string; grace_h: number; include_at_risk?: boolean }) =>
    request<PlanSummary>(`${sc(sid)}/plans`, { method: "POST", body: JSON.stringify(body) }),
  decisions: (sid: string, history = false) => request<Decision[]>(`${sc(sid)}/decisions${q({ history })}`),
  decide: (sid: string, body: { shipment_id: string; action: Action; plan_id?: string; option_id?: string; note?: string; grace_h: number }) =>
    request<Decision>(`${sc(sid)}/decisions`, { method: "POST", body: JSON.stringify(body) }),
  evalPlan: (sid: string, planId: string) =>
    request<{ kpis: ReplayKpis; by_status: Record<string, number> }>(`${sc(sid)}/evaluation/plans/${encodeURIComponent(planId)}`),
  evalDecisions: (sid: string, grace_h: number) =>
    request<{ accepted: number; kpis: ReplayKpis }>(`${sc(sid)}/evaluation/decisions${q({ grace_h })}`),
  evalDetections: (sid: string, grace_h: number) =>
    request<{ precision: number; recall: number; f1: number; flagged: number; stranded: number }>(
      `${sc(sid)}/evaluation/detections${q({ grace_h })}`,
    ),
  benchmark: () => request<BenchmarkSummary>("/benchmark"),
}
