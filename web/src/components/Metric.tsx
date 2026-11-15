import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

export type Tone = "neutral" | "accent" | "good" | "warn" | "critical"

const TONE: Record<Tone, string> = {
  neutral: "var(--muted-foreground)",
  accent: "var(--s1)",
  good: "var(--good)",
  warn: "var(--warn)",
  critical: "var(--critical)",
}

/** A KPI tile: tinted icon chip, label, a large number, context, and an optional bar. */
export function Metric({ icon: Icon, label, value, sub, tone = "neutral", valueTone, progress, loading, hint, compact, className }: {
  icon: LucideIcon
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: Tone
  /** Colour the number too (e.g. critical counts). */
  valueTone?: Tone
  /** 0-1: a thin bar under the number. */
  progress?: number
  loading?: boolean
  hint?: string
  /** Smaller, for rows of five or more. */
  compact?: boolean
  className?: string
}) {
  const c = TONE[tone]
  return (
    <div className={cn("flex items-start surface", compact ? "gap-2.5 px-3.5 py-3" : "gap-3 px-4 py-3.5", className)} title={hint}>
      <span
        className={cn("mt-0.5 flex shrink-0 items-center justify-center rounded-xl", compact ? "size-8" : "size-9")}
        style={{ background: `color-mix(in oklab, ${c} 12%, transparent)`, color: c, boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${c} 16%, transparent)` }}
        aria-hidden
      >
        <Icon className="size-[18px]" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-muted-foreground">{label}</div>
        {loading ? (
          <Skeleton className="mt-1.5 h-7 w-16" />
        ) : (
          <div className={cn("leading-tight font-semibold tracking-[-0.02em] whitespace-nowrap tabular-nums", compact ? "text-[21px]" : "text-[26px]")} style={valueTone ? { color: TONE[valueTone] } : undefined}>
            {value}
          </div>
        )}
        {sub && <div className="truncate text-xs text-muted-foreground">{sub}</div>}
        {progress != null && !loading && (
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-foreground/[0.06]" role="presentation">
            <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.round(100 * Math.min(1, Math.max(0, progress)))}%`, background: c }} />
          </div>
        )}
      </div>
    </div>
  )
}

/** A page's title row: what this page is, one line on what it's for, and its actions. */
export function PageHeader({ title, description, children, className }: { title: string; description?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-x-4 gap-y-2", className)}>
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-[-0.02em]">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  )
}
