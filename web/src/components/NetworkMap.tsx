import "maplibre-gl/dist/maplibre-gl.css"

import type { Layer } from "@deck.gl/core"
import { PathStyleExtension } from "@deck.gl/extensions"
import { TripsLayer } from "@deck.gl/geo-layers"
import { PathLayer } from "@deck.gl/layers"
import { MapboxOverlay } from "@deck.gl/mapbox"
import { LngLatBounds, Map as MLMap, Marker, NavigationControl, setWorkerUrl } from "maplibre-gl"
// MapLibre looks for its worker next to its own module; once bundled that's wrong,
// so ship the (self-contained) worker as an asset and point MapLibre at it.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url"
import { useTheme } from "next-themes"
import { useEffect, useMemo, useRef, useState } from "react"

import type { Mode, NetworkGeo } from "@/lib/api"
import { cn } from "@/lib/utils"

// The base map is India and its neighbours from Natural Earth's India point-of-view
// edition (public/basemap/india.json, built by scripts/build_basemap.py): no tiles, no
// keys, works offline, and India's boundaries match the official map. MapLibre draws
// it; deck.gl draws the network on top as gentle curves, with traffic flowing along
// the plan's piggyback hops.

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

const BASEMAP_URL = `${import.meta.env.BASE_URL}basemap/india.json`
const CURVE = 0.14 // sideways bend, as a share of the lane's length
const SEGMENTS = 28
const LOOP = 1000 // animation loop for flowing traffic, in arbitrary time units

type RGBA = [number, number, number, number]
type Coord = [number, number]
type Path = { path: Coord[]; n?: number; timestamps?: number[] }

// Theme colours are oklch(), which neither MapLibre nor deck.gl can parse: let the
// browser convert them by painting one pixel.
const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true })!
function rgba(name: string, alpha = 1): RGBA {
  probe.clearRect(0, 0, 1, 1)
  probe.fillStyle = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888"
  probe.fillRect(0, 0, 1, 1)
  const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data
  return [r, g, b, Math.round(a * alpha)]
}
const css = (c: RGBA) => `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${(c[3] / 255).toFixed(3)})`

/** A quadratic curve from a to b. Both directions of a lane bend the same way, so a
 *  hop drawn a->b lies exactly on the lane drawn b->a. */
function curve(a: Coord, b: Coord): Coord[] {
  const flip = a[0] > b[0] || (a[0] === b[0] && a[1] > b[1])
  const [p, q] = flip ? [b, a] : [a, b]
  const dx = q[0] - p[0], dy = q[1] - p[1]
  const c: Coord = [(p[0] + q[0]) / 2 - dy * CURVE, (p[1] + q[1]) / 2 + dx * CURVE]
  const pts: Coord[] = []
  for (let i = 0; i <= SEGMENTS; i++) {
    const t = i / SEGMENTS, u = 1 - t
    pts.push([u * u * p[0] + 2 * u * t * c[0] + t * t * q[0], u * u * p[1] + 2 * u * t * c[1] + t * t * q[1]])
  }
  return flip ? pts.reverse() : pts
}

// One instance for the map's lifetime: a new extension object makes deck.gl rebuild the layer.
// Dash along the whole path: per-segment dashing (the default) can't show on finely
// sampled curves.
const DASH = new PathStyleExtension({ highPrecisionDash: true })

const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches

export function NetworkMap(props: NetworkMapProps) {
  const { geo, counts, selectedHub, closedHubs, booked, recovery, hops, dedicated, delayedLanes, onHubClick, className, compact } = props
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<MLMap | null>(null)
  const overlay = useRef<MapboxOverlay | null>(null)
  const markers = useRef<Record<string, Marker>>({})
  const clickRef = useRef(onHubClick)
  clickRef.current = onHubClick
  const { resolvedTheme } = useTheme()
  const [loaded, setLoaded] = useState(false)
  // Flow animation time lives outside React state: re-rendering every frame would starve
  // React Router's navigation transitions on busy maps (the replay page).
  const time = useRef(0)
  const layersAt = useRef<((t: number) => Layer[]) | null>(null)

  const hubs = useMemo(() => {
    const out: Record<string, { coord: Coord; name: string }> = {}
    for (const f of geo.features) {
      if (f.properties.kind === "hub") out[f.id] = { coord: f.geometry.coordinates as Coord, name: String(f.properties.name) }
    }
    return out
  }, [geo])

  /** A route through hubs, as one smooth path. */
  const route = useMemo(() => (ids: string[]): Coord[] => {
    const pts = ids.filter((h) => hubs[h])
    const out: Coord[] = []
    for (let i = 0; i + 1 < pts.length; i++) {
      const seg = curve(hubs[pts[i]].coord, hubs[pts[i + 1]].coord)
      out.push(...(i ? seg.slice(1) : seg))
    }
    return out
  }, [hubs])

  // ---------------------------------------------------------------- map, once

  useEffect(() => {
    if (!container.current) return
    const m = new MLMap({
      container: container.current,
      style: {
        version: 8,
        sources: { base: { type: "geojson", data: BASEMAP_URL } },
        layers: [
          { id: "sea", type: "background", paint: { "background-color": "#eaeff5" } },
          { id: "neighbours", type: "fill", source: "base", filter: ["==", ["get", "kind"], "neighbour"], paint: { "fill-color": "#e3e7ed" } },
          // A soft, wide blur under India's coast lifts the land off the sea.
          { id: "india-shadow", type: "line", source: "base", filter: ["==", ["get", "kind"], "india"], paint: { "line-color": "#000", "line-width": 14, "line-blur": 14, "line-opacity": 0.06, "line-translate": [0, 3] } },
          { id: "india", type: "fill", source: "base", filter: ["==", ["get", "kind"], "india"], paint: { "fill-color": "#fbfbf9" } },
          { id: "states", type: "line", source: "base", filter: ["==", ["get", "kind"], "state-line"], paint: { "line-color": "#e4e1da", "line-width": 0.8 } },
          { id: "india-edge", type: "line", source: "base", filter: ["==", ["get", "kind"], "india"], paint: { "line-color": "#cfd6e0", "line-width": 1 } },
        ],
      },
      center: [80.5, 21.5],
      zoom: 3.6,
      minZoom: 3,
      maxZoom: 9,
      maxBounds: [[55, -2], [108, 42]],
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      renderWorldCopies: false,
    })
    m.touchZoomRotate.disableRotation()
    if (!compact) m.addControl(new NavigationControl({ showCompass: false }), "top-right")
    // deck.gl draws on its own canvas over the map; CSS lifts the hub markers above it.
    const ov = new MapboxOverlay({ interleaved: false, layers: [] })
    m.addControl(ov)
    map.current = m
    overlay.current = ov
    // An inline style can finish loading inside the constructor, before any listener
    // is attached: mark loaded now if so, else on whichever event fires first.
    const done = () => setLoaded(true)
    if (m.isStyleLoaded()) done()
    else {
      m.once("style.load", done)
      m.once("load", done)
    }
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(container.current)
    return () => {
      ro.disconnect()
      m.removeControl(ov)
      m.remove()
      map.current = null
      overlay.current = null
      markers.current = {}
      setLoaded(false)
    }
  }, [compact])

  // ---------------------------------------------------------------- base colours follow the theme

  useEffect(() => {
    const m = map.current
    if (!m || !loaded) return
    m.setPaintProperty("sea", "background-color", css(rgba("--map-sea")))
    m.setPaintProperty("neighbours", "fill-color", css(rgba("--map-neighbour")))
    m.setPaintProperty("india", "fill-color", css(rgba("--map-land")))
    m.setPaintProperty("states", "line-color", css(rgba("--map-state")))
    m.setPaintProperty("india-edge", "line-color", css(rgba("--map-edge")))
    m.setPaintProperty("india-shadow", "line-opacity", resolvedTheme === "dark" ? 0.35 : 0.06)
    // Marks a map whose layers loaded and accepted their colours (end-to-end tests check it).
    if (container.current) container.current.dataset.mapReady = "true"
  }, [loaded, resolvedTheme])

  // ---------------------------------------------------------------- camera

  useEffect(() => {
    const m = map.current
    if (!m || !loaded) return
    const pts = Object.values(hubs).map((h) => h.coord)
    if (pts.length) {
      const b = pts.reduce((acc, p) => acc.extend(p), new LngLatBounds(pts[0], pts[0]))
      m.fitBounds(b, { padding: compact ? 28 : { top: 56, bottom: 56, left: 56, right: 56 }, animate: false })
    }
  }, [loaded, hubs, compact])

  // ---------------------------------------------------------------- flowing traffic (respects reduced motion)

  const flowing = Boolean(hops?.length) && !compact
  useEffect(() => {
    if (!flowing || prefersReducedMotion()) return
    let raf = 0
    const t0 = performance.now()
    let last = t0, slow = 0
    const tick = (now: number) => {
      // A device that can't draw the flow smoothly (software WebGL, a weak GPU) stops it rather
      // than let it starve the page: after 5 slow frames in a row the traffic holds still.
      slow = now - last > 120 ? slow + 1 : 0
      last = now
      if (slow >= 5) return
      time.current = ((now - t0) / 9) % LOOP // one trip along a hop every ~9 s
      if (overlay.current && layersAt.current) overlay.current.setProps({ layers: layersAt.current(time.current) })
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [flowing])

  // ---------------------------------------------------------------- network layers

  const data = useMemo(() => {
    const seen = new Set<string>()
    const lanes: Path[] = []
    for (const f of geo.features) {
      if (f.properties.kind !== "lane") continue
      const [a, b] = [String(f.properties.from_hub), String(f.properties.to_hub)]
      const key = [a, b].sort().join("|")
      if (seen.has(key) || !hubs[a] || !hubs[b]) continue
      seen.add(key)
      lanes.push({ path: curve(hubs[a].coord, hubs[b].coord) })
    }
    const max = Math.max(1, ...(hops ?? []).map((h) => h.n))
    const hopPaths: Path[] = (hops ?? []).filter((h) => hubs[h.a] && hubs[h.b]).map((h) => {
      const path = curve(hubs[h.a].coord, hubs[h.b].coord)
      return { path, n: h.n / max, timestamps: path.map((_, i) => (i / (path.length - 1)) * LOOP) }
    })
    return {
      lanes,
      hops: hopPaths,
      dedicated: (dedicated ?? []).map((r) => ({ path: route(r) })),
      delays: (delayedLanes ?? []).filter((d) => hubs[d.from] && hubs[d.to]).map((d) => ({ path: curve(hubs[d.from].coord, hubs[d.to].coord) })),
      booked: booked && booked.length > 1 ? [{ path: route(booked) }] : [],
      recovery: recovery && recovery.route.length > 1 ? [{ path: route(recovery.route) }] : [],
    }
  }, [geo, hubs, hops, dedicated, delayedLanes, booked, recovery, route])

  useEffect(() => {
    const ov = overlay.current
    if (!ov || !loaded) return
    const dark = resolvedTheme === "dark"
    const s1 = rgba("--s1"), s2 = rgba("--s2")
    const common = { widthUnits: "pixels" as const, capRounded: true, jointRounded: true, getPath: (d: Path) => d.path, pickable: false }
    const layers = (t: number): Layer[] => [
      new PathLayer<Path>({ ...common, id: "lanes", data: data.lanes, getColor: rgba("--map-lane", compact ? 0.8 : 1), getWidth: compact ? 1 : 1.25 }),
      // Plan traffic: a wide, faint underglow and a crisp line, both scaled by parcels.
      new PathLayer<Path>({ ...common, id: "hops-glow", data: data.hops, getColor: [s1[0], s1[1], s1[2], dark ? 50 : 34], getWidth: (d) => 4 + 10 * (d.n ?? 0) }),
      new PathLayer<Path>({ ...common, id: "hops", data: data.hops, getColor: [s1[0], s1[1], s1[2], 220], getWidth: (d) => 1.5 + 5 * (d.n ?? 0) }),
      new TripsLayer<Path>({
        ...common, id: "flow", data: data.hops, getTimestamps: (d) => d.timestamps!, currentTime: t, trailLength: 180,
        getColor: dark ? [235, 242, 255, 230] : [255, 255, 255, 235], getWidth: (d) => 1 + 2.5 * (d.n ?? 0), fadeTrail: true,
      }),
      new PathLayer<Path>({ ...common, id: "dedicated", data: data.dedicated, getColor: [s2[0], s2[1], s2[2], 230], getWidth: 2, getDashArray: [4, 3], dashJustified: true, extensions: [DASH] } as never),
      new PathLayer<Path>({ ...common, id: "delays", data: data.delays, getColor: rgba("--serious", 0.95), getWidth: 2.5, getDashArray: [3, 2.5], extensions: [DASH] } as never),
      new PathLayer<Path>({ ...common, id: "booked", data: data.booked, getColor: rgba("--muted-foreground", 0.9), getWidth: 2, getDashArray: [1, 2.5], extensions: [DASH] } as never),
      new PathLayer<Path>({ ...common, id: "recovery-glow", data: data.recovery, getColor: [...(recovery?.mode === "DEDICATED" ? s2 : s1).slice(0, 3), 55] as RGBA, getWidth: 12 }),
      new PathLayer<Path>({
        // Always dash-capable (toggling extensions on a live layer breaks it); [0, 0] = solid.
        ...common, id: "recovery", data: data.recovery, getColor: recovery?.mode === "DEDICATED" ? s2 : s1, getWidth: 3.5,
        getDashArray: recovery?.mode === "DEDICATED" ? [3, 1.6] : [0, 0], extensions: [DASH],
        updateTriggers: { getDashArray: recovery?.mode, getColor: [recovery?.mode, resolvedTheme] },
      } as never),
    ]
    layersAt.current = layers
    ov.setProps({ layers: layers(time.current) })
  }, [loaded, data, resolvedTheme, recovery, compact])

  // ---------------------------------------------------------------- hub markers (real buttons: keyboard + screen readers)

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
        el.innerHTML = `<span class="pb-hub-dot"><span class="pb-hub-core"></span></span><span class="pb-hub-label">${id}</span>`
        el.addEventListener("click", (e) => {
          e.stopPropagation()
          clickRef.current?.(id)
        })
        mk = new Marker({ element: el }).setLngLat(h.coord).addTo(m)
        markers.current[id] = mk
      }
      const el = mk.getElement()
      const n = counts?.[id] ?? 0
      const size = compact ? 9 : 10 + 20 * Math.sqrt(n / max)
      const dot = el.querySelector<HTMLSpanElement>(".pb-hub-dot")!
      dot.style.width = dot.style.height = `${size}px`
      el.dataset.selected = String(id === selectedHub)
      el.dataset.closed = String(Boolean(closedHubs?.has(id)))
      el.dataset.onroute = String(Boolean(recovery?.route.includes(id) || booked?.includes(id)))
      el.dataset.busy = String(n > 0)
      el.dataset.compact = String(Boolean(compact))
      el.setAttribute("aria-label", `${id}, ${h.name}${n ? `, ${n} flagged` : ""}${closedHubs?.has(id) ? ", closed" : ""}`)
      el.title = el.getAttribute("aria-label")!
      el.tabIndex = onHubClick ? 0 : -1
    }
  }, [hubs, counts, selectedHub, closedHubs, recovery, booked, compact, onHubClick, loaded])

  return (
    <div ref={container} className={cn("pb-map relative h-full w-full", className)} role="region" aria-label="Network map">
      <span className="pointer-events-none absolute right-2 bottom-1.5 z-10 text-[10px] text-muted-foreground/70">Boundaries: Natural Earth</span>
    </div>
  )
}
