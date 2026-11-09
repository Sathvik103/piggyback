// Scenario times are hours after the scenario's start_at (an ISO time with an
// offset, IST by default). Show them as wall-clock times in that same zone.

const IST = "Asia/Kolkata"

export function clockAt(startAt: string, hours: number): Date {
  return new Date(new Date(startAt).getTime() + hours * 3_600_000)
}

const timeFmt = new Intl.DateTimeFormat("en-IN", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: false })
const dayTimeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST,
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
})
const dayFmt = new Intl.DateTimeFormat("en-IN", { timeZone: IST, weekday: "short", day: "numeric", month: "short" })
const weekdayFmt = new Intl.DateTimeFormat("en-IN", { timeZone: IST, weekday: "short" })

/** "Tue 3 Nov, 14:30" */
export function dayTime(startAt: string, hours: number): string {
  return dayTimeFmt.format(clockAt(startAt, hours))
}

/** "14:30", or "Wed 14:30" when it isn't the same day as `nowH`. */
export function shortTime(startAt: string, hours: number, nowH: number): string {
  const t = clockAt(startAt, hours)
  const sameDay = dayFmt.format(t) === dayFmt.format(clockAt(startAt, nowH))
  return sameDay ? timeFmt.format(t) : `${weekdayFmt.format(t)} ${timeFmt.format(t)}`
}

/** "in 9 h", "in 40 min", "2.5 h ago" */
export function relative(hours: number): string {
  const a = Math.abs(hours)
  const v = a < 1 ? `${Math.round(a * 60)} min` : `${a < 10 ? a.toFixed(1).replace(/\.0$/, "") : Math.round(a)} h`
  return hours >= 0 ? `in ${v}` : `${v} ago`
}

export function hoursText(h: number): string {
  if (h < 1) return `${Math.round(h * 60)} min`
  return `${h < 10 ? h.toFixed(1).replace(/\.0$/, "") : Math.round(h)} h`
}

const inrFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 })

/** "₹1,23,456" (Indian digit grouping) */
export function inr(x: number): string {
  return `${x < 0 ? "−" : ""}₹${inrFmt.format(Math.abs(Math.round(x)))}`
}

/** Large amounts as "₹12.4 lakh". */
export function lakh(x: number): string {
  return `₹${(x / 1e5).toFixed(x >= 1e6 ? 1 : 2)} lakh`
}

export function pct(x: number | null | undefined, digits = 1): string {
  return x == null ? "—" : `${x.toFixed(digits)}%`
}

/** A signed delta with a real minus sign: "−25.4%", "+4.0 pp" */
export function signed(x: number | null | undefined, unit = "", digits = 1): string {
  if (x == null) return "—"
  const s = Math.abs(x).toFixed(digits)
  return `${x < 0 ? "−" : x > 0 ? "+" : ""}${s}${unit}`
}

export function words(code: string | null | undefined): string {
  if (!code) return ""
  const s = code.replace(/_/g, " ").toLowerCase()
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export const KIND_INFO: Record<string, string> = {
  MISSED_CONNECTION: "Its truck arrived after the next leg left",
  LOST_SCAN: "The expected unload scan never came",
  MISROUTE: "Scanned onto a trip outside its booking",
  HELD: "On hold at a hub",
  MISSORT: "Part of a pile-up at one hub",
  STALLED: "Its next load is overdue",
  AT_RISK: "On a late truck that will miss its connection",
}

export const MODE_LABEL: Record<string, string> = {
  PIGGYBACK: "Piggyback",
  DEDICATED: "Dedicated truck",
  LOCAL: "Deliver locally",
  NONE: "Leave it",
}

export const PRIORITY_RANK: Record<string, number> = { EXPRESS: 0, STANDARD: 1, ECONOMY: 2 }
