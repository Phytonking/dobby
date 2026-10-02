'use client'

import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Calendar, BookOpen, Instagram, Linkedin } from 'lucide-react'
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

interface Notice {
  kind: 'success' | 'error'
  text: string
}

const PROVIDERS: ProviderConfig[] = [
  {
    key: 'google_calendar',
    label: 'Google Calendar',
    description: "The team calendar Dobby creates events on. Invitees don't need to connect anything.",
    icon: <Calendar className="h-6 w-6 text-blue-400" />,
  },
  {
    key: 'notion',
    label: 'Notion',
    description: 'The workspace account Dobby reads and writes Notion pages with.',
    icon: <BookOpen className="h-6 w-6 text-zinc-100" />,
  },
  {
    key: 'instagram',
    label: 'Instagram',
    description:
      "The group's Business/Creator account Dobby posts and stories to. Every post is confirmed in Discord first.",
    icon: <Instagram className="h-6 w-6 text-pink-400" />,
  },
  {
    key: 'linkedin',
    label: 'LinkedIn',
    description: "The account Dobby publishes LinkedIn posts from, after a Confirm in Discord.",
    icon: <Linkedin className="h-6 w-6 text-sky-400" />,
  },
]

const LABELS = new Map(PROVIDERS.map((p) => [p.key, p.label]))

// Only known provider keys render, so a crafted ?error= link can't inject text.
function readCallbackNotice(): Notice | null {
  const params = new URLSearchParams(window.location.search)
  const connected = LABELS.get(params.get('connected') ?? '')
  const failed = LABELS.get(params.get('error') ?? '')
  if (connected) return { kind: 'success', text: `${connected} connected.` }
  if (failed) {
    return { kind: 'error', text: `${failed} didn't connect. The sign-in was cancelled or failed — try again.` }
  }
  return null
}

function NoticeBanner({ notice }: { notice: Notice }) {
  const tone =
    notice.kind === 'success'
      ? 'border-emerald-800 bg-emerald-950 text-emerald-300'
      : 'border-red-800 bg-red-950 text-red-300'
  return (
    <div role="status" className={`rounded-md border px-4 py-3 text-sm ${tone}`}>
      {notice.text}
    </div>
  )
}

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
  const [notice, setNotice] = useState<Notice | null>(null)

  useEffect(() => {
    const fromCallback = readCallbackNotice()
    if (fromCallback) {
      setNotice(fromCallback)
      window.history.replaceState(null, '', window.location.pathname)
    }
  }, [])

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const isAdmin = me?.role === 'admin'

  const { data: integrations, isLoading, isError } = useQuery({
    queryKey: ['integrations'],
    queryFn: api.integrations.list,
    enabled: isAdmin,
  })

  const disconnectMutation = useMutation({
    mutationFn: (provider: string) => api.integrations.disconnect(provider),
    onSuccess: (_, provider) =>
      setNotice({ kind: 'success', text: `${LABELS.get(provider)} disconnected. Dobby no longer has access.` }),
    onError: (err, provider) =>
      setNotice({ kind: 'error', text: `Couldn't disconnect ${LABELS.get(provider)}: ${err.message}` }),
    // Refetch either way: a partial failure can still have removed some connections.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['integrations'] }),
  })

  function handleDisconnect(key: string) {
    const label = LABELS.get(key)
    if (window.confirm(`Disconnect ${label} for everyone? Dobby loses access until an admin reconnects it.`)) {
      disconnectMutation.mutate(key)
    }
  }

  const integrationMap = new Map<string, Integration>(
    integrations?.map((i) => [i.provider, i]) ?? []
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-100">Dobby&apos;s service accounts</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Connect the accounts Dobby acts through. One shared connection per service — nobody
          links a personal account. Admins only.
        </p>
      </div>

      {notice && <NoticeBanner notice={notice} />}

      {me && !isAdmin ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-zinc-400">
            Only admins can manage Dobby&apos;s service accounts.
          </CardContent>
        </Card>
      ) : isLoading || !me ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
      ) : isError ? (
        // Not "Disconnected" cards: an admin would reconnect live connections and create duplicates.
        <NoticeBanner notice={{ kind: 'error', text: "Couldn't load Dobby's service accounts. Refresh to try again." }} />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {PROVIDERS.map((provider) => (
            <IntegrationCard
              key={provider.key}
              provider={provider}
              integration={integrationMap.get(provider.key)}
              onDisconnect={handleDisconnect}
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
