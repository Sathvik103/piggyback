import { useQuery } from "@tanstack/react-query"
import { FlaskConical, Plus } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { dayTime } from "@/lib/format"
import { useCreateScenario, useScenarios } from "@/lib/queries"
import { useScenario } from "@/lib/scenario"

const ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

export function LabPage() {
  const { sid, setSid } = useScenario()
  const scenarios = useScenarios()
  return (
    <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="space-y-4">
        <section className="surface">
          <h2 className="border-b px-4 py-2.5 text-sm font-semibold">Scenarios</h2>
          {scenarios.isLoading ? (
            <Skeleton className="m-4 h-24" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Scenario</TableHead>
                  <TableHead>Now</TableHead>
                  <TableHead className="text-right">Shipments</TableHead>
                  <TableHead className="text-right">Stranded (truth)</TableHead>
                  <TableHead className="text-right">Late trucks</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {scenarios.data?.map((s) => (
                  <TableRow key={s.scenario_id}>
                    <TableCell className="font-medium">
                      {s.scenario_id} <span className="text-xs font-normal text-muted-foreground">seed {s.seed}</span>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{dayTime(s.start_at, s.now_h)}</TableCell>
                    <TableCell className="text-right tabular-nums">{s.shipments.toLocaleString("en-IN")}</TableCell>
                    <TableCell className="text-right tabular-nums">{s.stranded}</TableCell>
                    <TableCell className="text-right tabular-nums">{s.delayed_trips}</TableCell>
                    <TableCell className="text-right">
                      {s.scenario_id === sid ? <Badge variant="secondary">Active</Badge> : <Button variant="outline" size="sm" onClick={() => setSid(s.scenario_id)}>Switch</Button>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>
        <DetectionQuality />
      </div>
      <CreateScenario onCreated={setSid} />
    </div>
  )
}

function DetectionQuality() {
  const { sid, grace } = useScenario()
  const [on, setOn] = useState(false)
  const q = useQuery({ queryKey: ["eval-detections", sid, grace], queryFn: () => api.evalDetections(sid!, grace), enabled: on && Boolean(sid) })
  return (
    <section className="surface surface-dashed p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm">
          <FlaskConical className="size-4 text-muted-foreground" aria-hidden />
          <span className="font-medium">Detection quality</span>
          <span className="text-muted-foreground">Scored against the hidden ground truth at grace {grace} h.</span>
        </div>
        {!on && <Button variant="outline" size="sm" onClick={() => setOn(true)}>Score</Button>}
      </div>
      {on && (q.data ? (
        <div className="mt-3 grid grid-cols-3 gap-3 text-sm">
          <Metric label="Flags that are real (precision)" value={`${Math.round(q.data.precision * 100)}%`} />
          <Metric label="Stranded parcels caught (recall)" value={`${Math.round(q.data.recall * 100)}%`} />
          <Metric label="F1" value={q.data.f1.toFixed(2)} />
        </div>
      ) : <Skeleton className="mt-3 h-14" />)}
    </section>
  )
}

function CreateScenario({ onCreated }: { onCreated: (sid: string) => void }) {
  const create = useCreateScenario()
  const [f, setF] = useState({
    scenario_id: "", seed: "7", n_shipments: "3000", capacity_tightness: "0.5", anomaly_rate: "0.04",
    trip_delay_rate: "0.2", scan_lag_rate: "0.05", delay_mode: "trip", cancel_rate: "0", capacity_correlation: "0",
  })
  const [error, setError] = useState<string | null>(null)
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    setF({ ...f, [k]: e.target.value })
    setError(null)
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!ID_RE.test(f.scenario_id)) {
      setError("Use lowercase letters, digits, - or _ (start with a letter or digit).")
      return
    }
    const num = (k: keyof typeof f) => Number(f[k])
    create.mutate(
      {
        scenario_id: f.scenario_id, seed: num("seed"), n_shipments: num("n_shipments"), capacity_tightness: num("capacity_tightness"),
        anomaly_rate: num("anomaly_rate"), trip_delay_rate: num("trip_delay_rate"), scan_lag_rate: num("scan_lag_rate"),
        delay_mode: f.delay_mode, cancel_rate: num("cancel_rate"), capacity_correlation: num("capacity_correlation"),
      },
      {
        onSuccess: (s) => {
          toast.success(`Scenario ${s.scenario_id} created`, { description: `${s.shipments} shipments, ${s.stranded} stranded (truth)` })
          onCreated(s.scenario_id)
          setF({ ...f, scenario_id: "" })
        },
        onError: (err) => setError(err.message),
      },
    )
  }

  const field = (k: keyof typeof f, label: string, attrs: Record<string, string | number> = {}) => (
    <label className="grid gap-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Input value={f[k]} onChange={set(k)} inputMode="decimal" {...attrs} />
    </label>
  )

  return (
    <form onSubmit={submit} className="space-y-4 surface p-4" aria-label="Create a scenario">
      <h2 className="text-sm font-semibold">Generate a scenario</h2>
      {field("scenario_id", "Name", { placeholder: "seed7_tight", autoComplete: "off" })}
      <div className="grid grid-cols-2 gap-3">
        {field("seed", "Seed", { type: "number" })}
        {field("n_shipments", "Shipments", { type: "number", min: 50, max: 20000 })}
        {field("capacity_tightness", "Capacity tightness (0–0.99)")}
        {field("anomaly_rate", "Anomaly rate")}
        {field("trip_delay_rate", "Late trip share")}
        {field("scan_lag_rate", "Late scan share")}
      </div>
      <fieldset className="space-y-3 rounded-lg border p-3">
        <legend className="px-1 text-xs text-muted-foreground">Stress conditions</legend>
        <label className="grid gap-1 text-sm">
          <span className="text-muted-foreground">Delays start</span>
          <Select value={f.delay_mode} onValueChange={(v) => setF({ ...f, delay_mode: v })}>
            <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="trip">At departure</SelectItem>
              <SelectItem value="leg">Mid-trip (early scans look on time)</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <div className="grid grid-cols-2 gap-3">
          {field("cancel_rate", "Silent cancellations")}
          {field("capacity_correlation", "Peak scarcity (0–1)")}
        </div>
      </fieldset>
      {error && <p className="text-sm text-critical" role="alert">{error}</p>}
      <Button type="submit" className="w-full" disabled={create.isPending}>
        <Plus className="size-4" aria-hidden /> {create.isPending ? "Generating…" : "Generate scenario"}
      </Button>
    </form>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted/50 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold tabular-nums">{value}</div>
    </div>
  )
}
