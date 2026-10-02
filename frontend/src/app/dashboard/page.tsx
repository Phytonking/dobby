'use client'

import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Mail } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'

function CalendarEmailCard() {
  const queryClient = useQueryClient()
  const { data: user, isLoading } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const [value, setValue] = useState('')

  useEffect(() => {
    setValue(user?.calendar_email ?? '')
  }, [user?.calendar_email])

  const mutation = useMutation({
    mutationFn: (calendar_email: string | null) => api.updateMe({ calendar_email }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['me'] }),
  })

  if (isLoading) return <Skeleton className="h-44 w-full" />

  const saved = user?.calendar_email ?? ''
  const dirty = value.trim() !== saved

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <Mail className="h-5 w-5 text-indigo-400" />
          <CardTitle className="text-base">Calendar email</CardTitle>
        </div>
        <CardDescription className="mt-2">
          This is the address Dobby invites when someone asks to include you in a meeting.
          You can also set it in Discord with <code className="text-zinc-300">/email</code>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-3 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault()
            mutation.mutate(value.trim() || null)
          }}
        >
          <div className="grid flex-1 gap-2">
            <Label htmlFor="calendar_email">Email</Label>
            <Input
              id="calendar_email"
              type="email"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="you@uw.edu"
            />
          </div>
          <Button type="submit" size="sm" disabled={!dirty || mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Save'}
          </Button>
        </form>
        {mutation.isError && (
          <p className="mt-2 text-sm text-red-400">{(mutation.error as Error).message}</p>
        )}
        {!saved && !mutation.isPending && (
          <p className="mt-2 text-xs text-zinc-500">
            No address yet — Dobby cannot invite you until you add one.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function MeCard() {
  const { data: user, isLoading } = useQuery({ queryKey: ['me'], queryFn: api.me })

  if (isLoading) return <Skeleton className="h-20 w-full" />

  return (
    <div>
      <h1 className="text-2xl font-bold text-zinc-100">
        Welcome back, {user?.display_name ?? 'there'}.
      </h1>
      <p className="mt-1 text-sm text-zinc-400">
        Keep your calendar email current so Dobby can invite you to meetings.
      </p>
    </div>
  )
}

export default function DashboardPage() {
  return (
    <div className="space-y-8">
      <MeCard />
      <div className="max-w-xl">
        <CalendarEmailCard />
      </div>
    </div>
  )
}
