import "./index.css"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { lazy, StrictMode, Suspense, type ReactNode } from "react"
import { createRoot } from "react-dom/client"
import { createHashRouter, RouterProvider } from "react-router"

import { AppShell } from "@/components/AppShell"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AuthGate } from "@/lib/auth"
import { ScenarioProvider } from "@/lib/scenario"
import { QueuePage } from "@/pages/QueuePage"

// The queue loads first; the other pages (and MapLibre) load when visited.
const NetworkPage = lazy(() => import("@/pages/NetworkPage").then((m) => ({ default: m.NetworkPage })))
const PlansPage = lazy(() => import("@/pages/PlansPage").then((m) => ({ default: m.PlansPage })))
const BenchmarkPage = lazy(() => import("@/pages/BenchmarkPage").then((m) => ({ default: m.BenchmarkPage })))
const LabPage = lazy(() => import("@/pages/LabPage").then((m) => ({ default: m.LabPage })))

const page = (el: ReactNode) => <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Loading…</div>}>{el}</Suspense>

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false } },
})

// Hash routing: the API owns every real path, so the app never collides with it.
const router = createHashRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <QueuePage /> },
      { path: "network", element: page(<NetworkPage />) },
      { path: "plans", element: page(<PlansPage />) },
      { path: "benchmark", element: page(<BenchmarkPage />) },
      { path: "lab", element: page(<LabPage />) },
    ],
  },
])

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delayDuration={300}>
          <AuthGate>
            <ScenarioProvider>
              <RouterProvider router={router} />
            </ScenarioProvider>
          </AuthGate>
          <Toaster richColors position="bottom-right" />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
