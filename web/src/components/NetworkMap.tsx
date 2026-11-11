import "maplibre-gl/dist/maplibre-gl.css"

import type { Feature, FeatureCollection } from "geojson"
import { type GeoJSONSource, LngLatBounds, Map as MLMap, Marker, NavigationControl, setWorkerUrl } from "maplibre-gl"
// MapLibre looks for its worker next to its own module; once bundled that's wrong,
// so ship the (self-contained) worker as an asset and point MapLibre at it.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url"
import { useTheme } from "next-themes"
import { useEffect, useMemo, useRef } from "react"

import type { Mode, NetworkGeo } from "@/lib/api"
import { cn } from "@/lib/utils"

// No basemap tiles on purpose: the lane network itself shows the geography, it
// works offline, and it avoids drawing contested borders from a world basemap.

export interface Hop {
  a: string
  b: string
  n: number
}

export interface NetworkMapProps {
  geo: NetworkGeo
  counts?: Record<string, number>
  selectedHub?: string | null
  closedHubs?: Set<string>
  /** The shipment's original booking (dotted). */
  booked?: string[] | null
  /** The recovery being looked at (bold). */
  recovery?: { route: string[]; mode: Mode } | null
  /** A whole plan's piggyback hops, width by parcels. */
  hops?: Hop[]
  dedicated?: string[][]
  delayedLanes?: { from: string; to: string; label: string }[]
  onHubClick?: (hub: string) => void
  className?: string
  compact?: boolean
}

setWorkerUrl(workerUrl)

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] }

// Theme colours are oklch(), which MapLibre can't parse: let the browser convert
// them to rgba by painting one pixel.
const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true })!
function cssVar(name: string): string {
  probe.clearRect(0, 0, 1, 1)
  probe.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888"
  probe.fillRect(0, 0, 1, 1)
  const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data
  return `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`
}

export function NetworkMap(props: NetworkMapProps) {
  const { geo, counts, selectedHub, closedHubs, booked, recovery, hops, dedicated, delayedLanes, onHubClick, className, compact } = props
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<MLMap | null>(null)
  const ready = useRef<Promise<void> | null>(null)
  const markers = useRef<Record<string, Marker>>({})
  const clickRef = useRef(onHubClick)
  clickRef.current = onHubClick
  const { resolvedTheme } = useTheme()

  const hubs = useMemo(() => {
    const out: Record<string, { coord: [number, number]; name: string }> = {}
    for (const f of geo.features) {
      if (f.properties.kind === "hub") out[f.id] = { coord: f.geometry.coordinates as [number, number], name: String(f.properties.name) }
    }
    return out
  }, [geo])

  const line = (ids: string[], props: Record<string, unknown> = {}): Feature => ({
    type: "Feature",
    properties: props,
    geometry: { type: "LineString", coordinates: ids.filter((h) => hubs[h]).map((h) => hubs[h].coord) },
  })

  // Create the map once.
  useEffect(() => {
    if (!container.current) return
    const m = new MLMap({
      container: container.current,
      style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "rgba(0,0,0,0)" } }] },
      center: [80.5, 21.5],
      zoom: 3.6,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
    })
    m.touchZoomRotate.disableRotation()
    if (!compact) m.addControl(new NavigationControl({ showCompass: false }), "top-right")
    map.current = m
    // An inline style can finish loading inside the constructor, before any listener
    // is attached, so set up now if it's ready and otherwise on whichever event fires.
    ready.current = new Promise((resolve) => {
      const setup = () => {
        if (m.getSource("lanes")) return resolve()
        for (const id of ["lanes", "delays", "hops", "dedicated", "booked", "recovery"]) m.addSource(id, { type: "geojson", data: EMPTY })
        m.addLayer({ id: "lanes", type: "line", source: "lanes", paint: { "line-width": 1 } })
        m.addLayer({
          id: "hops", type: "line", source: "hops", layout: { "line-cap": "round" },
          paint: { "line-width": ["interpolate", ["linear"], ["get", "n"], 1, 1.5, 15, 7], "line-opacity": 0.8 },
        })
        m.addLayer({ id: "dedicated", type: "line", source: "dedicated", paint: { "line-width": 2, "line-dasharray": [3, 2] } })
        m.addLayer({ id: "booked", type: "line", source: "booked", paint: { "line-width": 2, "line-dasharray": [0.6, 1.8] }, layout: { "line-cap": "round" } })
        m.addLayer({ id: "recovery", type: "line", source: "recovery", paint: { "line-width": 4 }, layout: { "line-cap": "round", "line-join": "round" } })
        m.addLayer({ id: "delays", type: "line", source: "delays", paint: { "line-width": 3, "line-dasharray": [2, 1.5] } })
        resolve()
      }
      if (m.isStyleLoaded()) setup()
      else {
        m.once("style.load", setup)
        m.once("load", setup)
      }
    })
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(container.current)
    return () => {
      ro.disconnect()
      m.remove()
      map.current = null
      markers.current = {}
    }
  }, [compact])

  // Colours follow the theme.
  useEffect(() => {
    ready.current?.then(() => {
      const m = map.current
      if (!m) return
      m.setPaintProperty("lanes", "line-color", cssVar("--lane"))
      m.setPaintProperty("hops", "line-color", cssVar("--s1"))
      m.setPaintProperty("dedicated", "line-color", cssVar("--s2"))
      m.setPaintProperty("booked", "line-color", cssVar("--muted-foreground"))
      m.setPaintProperty("recovery", "line-color", cssVar("--s1"))
      m.setPaintProperty("delays", "line-color", cssVar("--serious"))
      // Marks a map whose layers loaded and accepted their colours (end-to-end tests check it).
      if (container.current) container.current.dataset.mapReady = "true"
    })
  }, [resolvedTheme])

  // Network geometry and the camera.
  useEffect(() => {
    ready.current?.then(() => {
      const m = map.current
      if (!m) return
      ;(m.getSource("lanes") as GeoJSONSource).setData({
        type: "FeatureCollection",
        features: geo.features.filter((f) => f.properties.kind === "lane") as Feature[],
      })
      const pts = Object.values(hubs).map((h) => h.coord)
      if (pts.length) {
        const b = pts.reduce((acc, p) => acc.extend(p), new LngLatBounds(pts[0], pts[0]))
        m.fitBounds(b, { padding: compact ? 28 : 56, animate: false })
      }
    })
  }, [geo, hubs, compact])

  // Overlays.
  useEffect(() => {
    ready.current?.then(() => {
      const m = map.current
      if (!m) return
      const set = (id: string, features: Feature[]) => (m.getSource(id) as GeoJSONSource).setData({ type: "FeatureCollection", features })
      set("hops", (hops ?? []).map((h) => line([h.a, h.b], { n: h.n })))
      set("dedicated", (dedicated ?? []).map((r) => line(r)))
      set("booked", booked && booked.length > 1 ? [line(booked)] : [])
      set("recovery", recovery && recovery.route.length > 1 ? [line(recovery.route)] : [])
      set("delays", (delayedLanes ?? []).map((d) => line([d.from, d.to], { label: d.label })))
      m.setPaintProperty("recovery", "line-color", cssVar(recovery?.mode === "DEDICATED" ? "--s2" : "--s1"))
      m.setPaintProperty("recovery", "line-dasharray", recovery?.mode === "DEDICATED" ? [2, 1.2] : [1, 0])
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hops, dedicated, booked, recovery, delayedLanes, hubs])

  // Hub markers: size = parcels flagged there; ring = selected or closed.
  useEffect(() => {
    const m = map.current
    if (!m) return
    const max = Math.max(1, ...Object.values(counts ?? {}))
    for (const [id, h] of Object.entries(hubs)) {
      let mk = markers.current[id]
      if (!mk) {
        const el = document.createElement("button")
        el.type = "button"
        el.className = "pb-hub"
        el.innerHTML = `<span class="pb-hub-dot"></span><span class="pb-hub-label">${id}</span>`
        el.addEventListener("click", (e) => {
          e.stopPropagation()
          clickRef.current?.(id)
        })
        mk = new Marker({ element: el }).setLngLat(h.coord).addTo(m)
        markers.current[id] = mk
      }
      const el = mk.getElement()
      const n = counts?.[id] ?? 0
      const size = compact ? 8 : 8 + 18 * Math.sqrt(n / max)
      const dot = el.querySelector<HTMLSpanElement>(".pb-hub-dot")!
      dot.style.width = dot.style.height = `${size}px`
      el.dataset.selected = String(id === selectedHub)
      el.dataset.closed = String(Boolean(closedHubs?.has(id)))
      el.dataset.onroute = String(Boolean(recovery?.route.includes(id) || booked?.includes(id)))
      el.dataset.compact = String(Boolean(compact))
      el.setAttribute("aria-label", `${id}, ${h.name}${n ? `, ${n} flagged` : ""}${closedHubs?.has(id) ? ", closed" : ""}`)
      el.title = el.getAttribute("aria-label")!
      el.tabIndex = onHubClick ? 0 : -1
    }
  }, [hubs, counts, selectedHub, closedHubs, recovery, booked, compact, onHubClick])

  return <div ref={container} className={cn("relative h-full w-full", className)} role="region" aria-label="Network map" />
}
