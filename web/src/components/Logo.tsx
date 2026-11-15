import { useId } from "react"

import { cn } from "@/lib/utils"

/** The Piggyback mark: a parcel riding a route. Same drawing as public/favicon.svg. */
export function LogoMark({ size = 36, className }: { size?: number; className?: string }) {
  const id = useId().replace(/:/g, "")
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={cn("shrink-0", className)} aria-hidden>
      <defs>
        <linearGradient id={`${id}-fill`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#5a8ee0" />
          <stop offset="1" stopColor="#2a55a2" />
        </linearGradient>
        <linearGradient id={`${id}-sheen`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity=".32" />
          <stop offset=".5" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill={`url(#${id}-fill)`} />
      <rect width="64" height="64" rx="16" fill={`url(#${id}-sheen)`} />
      <rect x=".5" y=".5" width="63" height="63" rx="15.5" fill="none" stroke="#fff" strokeOpacity=".18" />
      <path d="M12 41 H48" stroke="#fff" strokeWidth="5" strokeLinecap="round" />
      <path d="M41.5 33.5 L49.5 41 L41.5 48.5" fill="none" stroke="#fff" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
      <rect x="16" y="17" width="18" height="18" rx="4.5" fill="#fff" />
    </svg>
  )
}

export function Wordmark({ className }: { className?: string }) {
  return <span className={cn("text-[15px] font-semibold tracking-[-0.02em]", className)}>Piggyback</span>
}
