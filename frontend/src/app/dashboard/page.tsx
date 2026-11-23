'use client'

import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { Mail, UserRound } from 'lucide-react'
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
    onSuccess: (updated) => {
      // Show the stored (normalised) address straight away instead of waiting for a refetch.
      queryClient.setQueryData(['me'], updated)
      setValue(updated.calendar_email ?? '')
    },
  })

  if (isLoading) return <Skeleton className="h-44 w-full" />

  const saved = user?.calendar_email ?? ''
  const dirty = value.trim().toLowerCase() !== saved

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <Mail className="h-5 w-5 text-indigo-600" />
          <CardTitle className="text-base">Calendar email</CardTitle>
        </div>
        <CardDescription className="mt-2">
          This is the address Dobby invites when someone asks to include you in a meeting.
          You can also set it in Discord with <code className="text-foreground/80">/email</code>.
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
          <p role="alert" className="mt-2 text-sm text-red-600">{(mutation.error as Error).message}</p>
        )}
        {mutation.isSuccess && !dirty && (
          <p role="status" className="mt-2 text-sm text-emerald-600">
            {saved ? `Saved. Dobby will invite ${saved}.` : 'Removed.'}
          </p>
        )}
        {!saved && !mutation.isPending && (
          <p className="mt-2 text-xs text-muted-foreground">
            No address yet — Dobby cannot invite you until you add one.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function ProfileCard() {
  const queryClient = useQueryClient()
  const router = useRouter()
  const { data: user, isLoading } = useQuery({ queryKey: ['me'], queryFn: api.me })
  const [name, setName] = useState('')

  useEffect(() => {
    setName(user?.display_name ?? '')
  }, [user?.display_name])

  const mutation = useMutation({
    mutationFn: (display_name: string) => api.updateMe({ display_name }),
    onSuccess: (updated) => {
      queryClient.setQueryData(['me'], updated)
      router.refresh() // the sidebar is server-rendered with the old name
    },
  })

  if (isLoading || !user) return <Skeleton className="h-44 w-full" />

  const dirty = name.trim() !== (user.display_name ?? '') && name.trim() !== ''

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <UserRound className="h-5 w-5 text-indigo-600" />
          <CardTitle className="text-base">Your profile</CardTitle>
        </div>
        <CardDescription className="mt-2">
          How Dobby and your teammates see you. Ask an admin to change how you sign in.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form
          className="flex flex-col gap-3 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault()
            mutation.mutate(name.trim())
          }}
        >
          <div className="grid flex-1 gap-2">
            <Label htmlFor="display_name">Display name</Label>
            <Input id="display_name" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
          </div>
          <Button type="submit" size="sm" disabled={!dirty || mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Save name'}
          </Button>
        </form>
        {mutation.isError && (
          <p role="alert" className="text-sm text-red-600">{(mutation.error as Error).message}</p>
        )}
        <dl className="grid grid-cols-1 gap-3 rounded-lg bg-muted/60 p-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Google sign-in</dt>
            <dd className="mt-1 truncate text-foreground">{user.uw_email ?? 'Not linked'}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Discord ID</dt>
            <dd className="mt-1 truncate font-mono text-foreground">{user.discord_id ?? 'Not linked'}</dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  )
}

function MeCard() {
  const { data: user, isLoading } = useQuery({ queryKey: ['me'], queryFn: api.me })

  if (isLoading) return <Skeleton className="h-20 w-full" />

  return (
    <div>
      <h1 className="text-2xl font-bold text-foreground">
        Welcome back, {user?.display_name ?? 'there'}.
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Keep your details current so Dobby can find you and invite you to meetings.
      </p>
    </div>
  )
}

export default function DashboardPage() {
  return (
    <div className="space-y-8">
      <MeCard />
      <div className="grid max-w-5xl grid-cols-1 gap-6 lg:grid-cols-2">
        <CalendarEmailCard />
        <ProfileCard />
      </div>
    </div>
  )
}
