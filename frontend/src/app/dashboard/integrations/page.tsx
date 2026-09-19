'use client'

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Calendar, Github, BookOpen } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import type { Integration } from '@/lib/types'

interface ProviderConfig {
  key: string
  label: string
  description: string
  icon: React.ReactNode
}

const PROVIDERS: ProviderConfig[] = [
  {
    key: 'googlecalendar',
    label: 'Google Calendar',
    description: 'Let Dobby access your calendar to schedule events and check availability.',
    icon: <Calendar className="h-6 w-6 text-blue-400" />,
  },
  {
    key: 'github',
    label: 'GitHub',
    description: 'Connect GitHub to manage repos, issues, and pull requests via Dobby.',
    icon: <Github className="h-6 w-6 text-zinc-200" />,
  },
  {
    key: 'notion',
    label: 'Notion',
    description: 'Give Dobby access to read and write Notion pages and databases.',
    icon: <BookOpen className="h-6 w-6 text-zinc-100" />,
  },
]

function IntegrationCard({
  provider,
  integration,
  onDisconnect,
  isDisconnecting,
}: {
  provider: ProviderConfig
  integration: Integration | undefined
  onDisconnect: (key: string) => void
  isDisconnecting: boolean
}) {
  const isConnected = !!integration

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            {provider.icon}
            <div>
              <CardTitle className="text-base">{provider.label}</CardTitle>
            </div>
          </div>
          <Badge variant={isConnected ? 'success' : 'secondary'}>
            {isConnected ? 'Connected' : 'Disconnected'}
          </Badge>
        </div>
        <CardDescription className="mt-2">{provider.description}</CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        {isConnected ? (
          <div className="flex items-center justify-between">
            <p className="text-xs text-zinc-400">
              Since{' '}
              {new Date(integration.connected_at).toLocaleDateString('en-US', {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
              })}
            </p>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => onDisconnect(provider.key)}
              disabled={isDisconnecting}
            >
              {isDisconnecting ? 'Disconnecting…' : 'Disconnect'}
            </Button>
          </div>
        ) : (
          <Button asChild size="sm" variant="secondary">
            <a href={api.integrations.connectUrl(provider.key)}>Connect</a>
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

export default function IntegrationsPage() {
  const queryClient = useQueryClient()

  const { data: integrations, isLoading } = useQuery({
    queryKey: ['integrations'],
    queryFn: api.integrations.list,
  })

  const disconnectMutation = useMutation({
    mutationFn: (provider: string) => api.integrations.disconnect(provider),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['integrations'] }),
  })

  const integrationMap = new Map<string, Integration>(
    integrations?.map((i) => [i.provider, i]) ?? []
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-100">Integrations</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Connect external services to give Dobby access on your behalf.
        </p>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {PROVIDERS.map((provider) => (
            <IntegrationCard
              key={provider.key}
              provider={provider}
              integration={integrationMap.get(provider.key)}
              onDisconnect={(key) => disconnectMutation.mutate(key)}
              isDisconnecting={
                disconnectMutation.isPending && disconnectMutation.variables === provider.key
              }
            />
          ))}
        </div>
      )}
    </div>
  )
}
