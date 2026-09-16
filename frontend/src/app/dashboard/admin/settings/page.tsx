'use client'

import { useState, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Save } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { api } from '@/lib/api'
import type { GuildSettings } from '@/lib/types'

const MODELS = ['gemini-2.5-flash-lite', 'gemini-2.0-flash']

function csvToArray(csv: string): string[] {
  return csv
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

function arrayToCsv(arr: string[]): string {
  return arr.join(', ')
}

export default function SettingsPage() {
  const [guildId, setGuildId] = useState(process.env.NEXT_PUBLIC_GUILD_ID ?? '')
  const [submitted, setSubmitted] = useState(false)
  const [saveSuccess, setSaveSuccess] = useState(false)

  const [form, setForm] = useState<Omit<GuildSettings, 'guild_id'> & { guild_id: string }>({
    guild_id: guildId,
    timezone: 'America/Los_Angeles',
    model: 'gemini-2.5-flash-lite',
    allowed_role_ids: [],
    admin_role_ids: [],
    allowed_channel_ids: [],
    mention_channel_ids: [],
    context_limit: 20,
  })

  const [csvFields, setCsvFields] = useState({
    allowed_role_ids: '',
    admin_role_ids: '',
    allowed_channel_ids: '',
    mention_channel_ids: '',
  })

  const { data: settings, isLoading, refetch } = useQuery({
    queryKey: ['admin', 'settings', guildId],
    queryFn: () => api.admin.settings.get(guildId),
    enabled: submitted && !!guildId,
  })

  useEffect(() => {
    if (settings) {
      setForm(settings)
      setCsvFields({
        allowed_role_ids: arrayToCsv(settings.allowed_role_ids),
        admin_role_ids: arrayToCsv(settings.admin_role_ids),
        allowed_channel_ids: arrayToCsv(settings.allowed_channel_ids),
        mention_channel_ids: arrayToCsv(settings.mention_channel_ids),
      })
    }
  }, [settings])

  const saveMutation = useMutation({
    mutationFn: () =>
      api.admin.settings.update(guildId, {
        ...form,
        allowed_role_ids: csvToArray(csvFields.allowed_role_ids),
        admin_role_ids: csvToArray(csvFields.admin_role_ids),
        allowed_channel_ids: csvToArray(csvFields.allowed_channel_ids),
        mention_channel_ids: csvToArray(csvFields.mention_channel_ids),
      }),
    onSuccess: () => {
      setSaveSuccess(true)
      refetch()
      setTimeout(() => setSaveSuccess(false), 3000)
    },
  })

  function handleLoad() {
    setSubmitted(true)
    refetch()
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-100">Guild Settings</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Configure Dobby&apos;s behaviour for your Discord guild.
        </p>
      </div>

      {/* Guild ID loader */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Select guild</CardTitle>
          <CardDescription>Enter your Discord guild ID to load its settings.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-3">
            <Input
              value={guildId}
              onChange={(e) => setGuildId(e.target.value)}
              placeholder="Discord guild ID"
              className="max-w-xs font-mono"
            />
            <Button onClick={handleLoad} disabled={!guildId} variant="secondary">
              Load
            </Button>
          </div>
        </CardContent>
      </Card>

      {submitted && (
        <>
          {isLoading ? (
            <div className="space-y-4">
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Configuration</CardTitle>
                <CardDescription>Guild ID: {guildId}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
                  {/* Timezone */}
                  <div className="grid gap-2">
                    <Label htmlFor="timezone">Timezone</Label>
                    <Input
                      id="timezone"
                      value={form.timezone}
                      onChange={(e) => setForm((f) => ({ ...f, timezone: e.target.value }))}
                      placeholder="America/Los_Angeles"
                    />
                  </div>

                  {/* Model */}
                  <div className="grid gap-2">
                    <Label htmlFor="model">Model</Label>
                    <Select
                      value={form.model}
                      onValueChange={(v) => setForm((f) => ({ ...f, model: v }))}
                    >
                      <SelectTrigger id="model">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {MODELS.map((m) => (
                          <SelectItem key={m} value={m}>
                            {m}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Context limit */}
                  <div className="grid gap-2">
                    <Label htmlFor="context_limit">Context limit (messages)</Label>
                    <Input
                      id="context_limit"
                      type="number"
                      min={1}
                      max={200}
                      value={form.context_limit}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, context_limit: parseInt(e.target.value, 10) || 20 }))
                      }
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
                  {/* Allowed role IDs */}
                  <div className="grid gap-2">
                    <Label htmlFor="allowed_role_ids">Allowed role IDs</Label>
                    <Input
                      id="allowed_role_ids"
                      value={csvFields.allowed_role_ids}
                      onChange={(e) =>
                        setCsvFields((f) => ({ ...f, allowed_role_ids: e.target.value }))
                      }
                      placeholder="123456, 789012"
                      className="font-mono text-xs"
                    />
                    <p className="text-xs text-zinc-500">Comma-separated Discord role IDs</p>
                  </div>

                  {/* Admin role IDs */}
                  <div className="grid gap-2">
                    <Label htmlFor="admin_role_ids">Admin role IDs</Label>
                    <Input
                      id="admin_role_ids"
                      value={csvFields.admin_role_ids}
                      onChange={(e) =>
                        setCsvFields((f) => ({ ...f, admin_role_ids: e.target.value }))
                      }
                      placeholder="123456, 789012"
                      className="font-mono text-xs"
                    />
                    <p className="text-xs text-zinc-500">Comma-separated Discord role IDs</p>
                  </div>

                  {/* Allowed channel IDs */}
                  <div className="grid gap-2">
                    <Label htmlFor="allowed_channel_ids">Allowed channel IDs</Label>
                    <Input
                      id="allowed_channel_ids"
                      value={csvFields.allowed_channel_ids}
                      onChange={(e) =>
                        setCsvFields((f) => ({ ...f, allowed_channel_ids: e.target.value }))
                      }
                      placeholder="123456, 789012"
                      className="font-mono text-xs"
                    />
                    <p className="text-xs text-zinc-500">Comma-separated Discord channel IDs</p>
                  </div>

                  {/* Mention channel IDs */}
                  <div className="grid gap-2">
                    <Label htmlFor="mention_channel_ids">Mention channel IDs</Label>
                    <Input
                      id="mention_channel_ids"
                      value={csvFields.mention_channel_ids}
                      onChange={(e) =>
                        setCsvFields((f) => ({ ...f, mention_channel_ids: e.target.value }))
                      }
                      placeholder="123456, 789012"
                      className="font-mono text-xs"
                    />
                    <p className="text-xs text-zinc-500">Channels where Dobby responds to mentions</p>
                  </div>
                </div>

                <div className="flex items-center gap-3 pt-2">
                  <Button
                    onClick={() => saveMutation.mutate()}
                    disabled={saveMutation.isPending}
                    className="gap-2"
                  >
                    <Save className="h-4 w-4" />
                    {saveMutation.isPending ? 'Saving…' : 'Save settings'}
                  </Button>
                  {saveSuccess && (
                    <p className="text-sm text-emerald-400">Settings saved successfully.</p>
                  )}
                  {saveMutation.isError && (
                    <p className="text-sm text-red-400">{(saveMutation.error as Error).message}</p>
                  )}
                </div>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
