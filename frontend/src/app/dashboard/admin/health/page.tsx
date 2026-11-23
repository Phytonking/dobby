'use client'

import { useQuery } from '@tanstack/react-query'
import { Activity, AlertTriangle, CheckCircle2, Clock } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import type { IntegrationStatus, ServiceHealth } from '@/lib/types'

const SERVICE_LABELS: Record<string, string> = {
  google_calendar: 'Google Calendar',
  notion: 'Notion',
  instagram: 'Instagram',
  linkedin: 'LinkedIn',
  dobby: "Dobby's own tools",
}

const CONNECTION: Record<IntegrationStatus, { text: string; variant: 'success' | 'secondary' | 'warning' | 'error' }> = {
  connected: { text: 'Connected', variant: 'success' },
  pending: { text: 'Finishing sign-in', variant: 'warning' },
  needs_reconnect: { text: 'Needs reconnect', variant: 'error' },
  disconnected: { text: 'Not connected', variant: 'secondary' },
}

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return `${hours} h ago`
}

// Healthy unless it is disconnected/broken or more than a fifth of its calls failed.
function verdict(service: ServiceHealth): 'ok' | 'warn' | 'bad' | 'idle' {
  if (service.connection === 'needs_reconnect') return 'bad'
  if (service.calls > 0 && service.failures / service.calls > 0.2) return 'bad'
  if (service.failures > 0 || service.connection === 'pending') return 'warn'
  if (service.calls === 0) return 'idle'
  return 'ok'
}

function Tile({ label, value, hint, icon }: { label: string; value: string; hint?: string; icon: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="flex items-start gap-4 p-5">
        <div className="rounded-lg bg-accent p-2 text-accent-foreground">{icon}</div>
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{value}</p>
          {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  )
}

export default function HealthPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['admin', 'health'],
    queryFn: api.admin.health,
    refetchInterval: 60_000,
  })

  const peak = Math.max(1, ...(data?.hourly.map((h) => h.ok + h.failed) ?? [1]))
  const errorRate = data && data.calls > 0 ? Math.round((data.failures / data.calls) * 100) : 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">System health</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The last 24 hours: whether each service is connected and how Dobby&apos;s calls to it went.
          Refreshes every minute.
        </p>
      </div>

      {isError ? (
        <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          Couldn&apos;t load system health: {(error as Error).message}
        </p>
      ) : isLoading || !data ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : (
        <>
          {!data.composio_verified && (
            <p role="status" className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              Couldn&apos;t check connections with Composio, so connection badges show the dashboard&apos;s last
              known state.
            </p>
          )}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Tile label="Tool calls" value={String(data.calls)} hint="last 24 hours" icon={<Activity className="h-5 w-5" />} />
            <Tile
              label="Failures"
              value={String(data.failures)}
              hint={data.calls ? `${errorRate}% of calls` : undefined}
              icon={<AlertTriangle className="h-5 w-5" />}
            />
            <Tile
              label="Services connected"
              value={`${data.services.filter((s) => s.connection === 'connected').length} / ${
                data.services.filter((s) => s.connection !== null).length
              }`}
              icon={<CheckCircle2 className="h-5 w-5" />}
            />
            <Tile label="Last activity" value={ago(data.last_activity_at)} icon={<Clock className="h-5 w-5" />} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Calls per hour</CardTitle>
              <CardDescription>Successful calls in indigo, failures in red.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex h-36 items-end gap-1" role="img" aria-label="Tool calls per hour over the last 24 hours">
                {data.hourly.map((h) => (
                  <div
                    key={h.hour}
                    className="flex flex-1 flex-col justify-end"
                    style={{ height: '100%' }}
                    title={`${new Date(h.hour).toLocaleTimeString([], { hour: 'numeric' })}: ${h.ok} ok, ${h.failed} failed`}
                  >
                    {h.failed > 0 && (
                      <div className="rounded-t-sm bg-red-400" style={{ height: `${(h.failed / peak) * 100}%` }} />
                    )}
                    <div
                      className={h.failed > 0 ? 'bg-indigo-400' : 'rounded-t-sm bg-indigo-400'}
                      style={{ height: `${(h.ok / peak) * 100}%`, minHeight: h.ok ? 2 : 0 }}
                    />
                    <div className="h-px bg-border" />
                  </div>
                ))}
              </div>
              <div className="mt-2 flex justify-between text-xs text-muted-foreground">
                <span>24 h ago</span>
                <span>now</span>
              </div>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {data.services.map((service) => {
              const state = verdict(service)
              const dot = { ok: 'bg-emerald-500', warn: 'bg-amber-500', bad: 'bg-red-500', idle: 'bg-slate-300' }[state]
              return (
                <Card key={service.key} data-testid={`health-${service.key}`}>
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2">
                        <span className={`h-2.5 w-2.5 rounded-full ${dot}`} aria-hidden />
                        <CardTitle className="text-base">{SERVICE_LABELS[service.key] ?? service.key}</CardTitle>
                      </div>
                      {service.connection && (
                        <Badge variant={CONNECTION[service.connection].variant}>
                          {CONNECTION[service.connection].text}
                        </Badge>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    <dl className="grid grid-cols-2 gap-y-2 text-sm">
                      <dt className="text-muted-foreground">Calls</dt>
                      <dd className="text-right font-medium text-foreground">{service.calls}</dd>
                      <dt className="text-muted-foreground">Failures</dt>
                      <dd className={`text-right font-medium ${service.failures ? 'text-red-600' : 'text-foreground'}`}>
                        {service.failures}
                      </dd>
                      <dt className="text-muted-foreground">Last success</dt>
                      <dd className="text-right text-foreground">{ago(service.last_success_at)}</dd>
                      <dt className="text-muted-foreground">Last failure</dt>
                      <dd className="text-right text-foreground">{ago(service.last_failure_at)}</dd>
                    </dl>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
