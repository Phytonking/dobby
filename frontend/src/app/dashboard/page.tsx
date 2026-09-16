'use client'

import { useQuery } from '@tanstack/react-query'
import { Calendar, Github, BookOpen, Plug } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import type { Integration } from '@/lib/types'

const PROVIDER_META: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
  google_calendar: {
    label: 'Google Calendar',
    icon: <Calendar className="h-5 w-5 text-blue-400" />,
    color: 'text-blue-400',
  },
  github: {
    label: 'GitHub',
    icon: <Github className="h-5 w-5 text-zinc-300" />,
    color: 'text-zinc-300',
  },
  notion: {
    label: 'Notion',
    icon: <BookOpen className="h-5 w-5 text-zinc-100" />,
    color: 'text-zinc-100',
  },
}

function IntegrationCard({ integration }: { integration: Integration }) {
  const meta = PROVIDER_META[integration.provider] ?? {
    label: integration.provider,
    icon: <Plug className="h-5 w-5 text-zinc-400" />,
    color: 'text-zinc-400',
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          {meta.icon}
          <CardTitle className="text-base">{meta.label}</CardTitle>
          <Badge variant="success" className="ml-auto">
            Connected
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-xs text-zinc-400">
          Connected{' '}
          {new Date(integration.connected_at).toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
          })}
        </p>
      </CardContent>
    </Card>
  )
}

function MeCard() {
  const { data: user, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
  })

  if (isLoading) return <Skeleton className="h-20 w-full" />

  return (
    <div>
      <h1 className="text-2xl font-bold text-zinc-100">
        Welcome back, {user?.display_name ?? 'there'}.
      </h1>
      <p className="mt-1 text-sm text-zinc-400">
        Manage your integrations and check your recent activity.
      </p>
    </div>
  )
}

export default function DashboardPage() {
  const { data: integrations, isLoading } = useQuery({
    queryKey: ['integrations'],
    queryFn: api.integrations.list,
  })

  return (
    <div className="space-y-8">
      <MeCard />

      {/* Stats */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <CardContent className="pt-6">
            <p className="text-3xl font-bold text-zinc-100">{integrations?.length ?? '—'}</p>
            <p className="mt-1 text-sm text-zinc-400">Integrations connected</p>
          </CardContent>
        </Card>
      </div>

      {/* Connected integrations */}
      <div>
        <h2 className="mb-4 text-lg font-semibold text-zinc-100">Connected integrations</h2>
        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
        ) : integrations && integrations.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {integrations.map((integration) => (
              <IntegrationCard key={integration.provider} integration={integration} />
            ))}
          </div>
        ) : (
          <Card>
            <CardContent className="py-10 text-center">
              <Plug className="mx-auto mb-3 h-8 w-8 text-zinc-600" />
              <p className="text-sm text-zinc-400">No integrations connected yet.</p>
              <p className="mt-1 text-xs text-zinc-500">
                Go to <span className="font-medium text-zinc-300">Integrations</span> to connect your accounts.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
