'use client'

import Link from 'next/link'
import { Bot } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { api, API } from '@/lib/api'
import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useRouter } from 'next/navigation'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'


export default function LoginPage() {
  const router = useRouter()
  const { data: methods, isLoading, isError, error: methodsError } = useQuery({
    queryKey: ['login-methods'], queryFn: api.loginMethods,
  })
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    setError('')
    try {
      await api.localLogin(username, password)
      router.push('/dashboard')
      router.refresh()
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Unable to sign in')
    } finally {
      setPending(false)
    }
  }
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 px-4">
      <div className="w-full max-w-sm">
        {/* Logo / wordmark */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-500/10 ring-1 ring-indigo-500/30">
            <Bot className="h-7 w-7 text-indigo-400" />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-bold tracking-tight text-zinc-100">Dobby Dashboard</h1>
            <p className="mt-1.5 text-sm text-zinc-400">
              Team portal &mdash; pre-registration required.
            </p>
          </div>
        </div>

        {isLoading && <p role="status" className="text-sm text-zinc-400">Loading sign-in options...</p>}
        {/* Show what the API actually said — "Local network access only" and a CORS
            failure are the same symptom here, and only the detail tells them apart. */}
        {isError && (
          <p role="alert" className="text-sm text-red-400">
            Cannot reach the dashboard API
            {methodsError instanceof Error && methodsError.message ? ` — ${methodsError.message}` : ''}.
            {' '}Check the server address and connection, then reload.
          </p>
        )}
        {methods?.local && (
          <form onSubmit={login} className="mb-6 flex flex-col gap-3">
            <h2 className="text-lg font-medium text-zinc-100">Local account</h2>
            <Label htmlFor="username">Username</Label>
            <Input id="username" autoComplete="username" required maxLength={64}
              value={username} onChange={(event) => setUsername(event.target.value)} />
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete="current-password" required maxLength={256}
              value={password} onChange={(event) => setPassword(event.target.value)} />
            {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
            <Button type="submit" disabled={pending}>{pending ? 'Signing in...' : 'Sign in'}</Button>
            <p className="text-xs text-zinc-500">Use an account created by your server administrator. Local network access required.</p>
          </form>
        )}
        {methods?.oauth && (
          <div className="flex flex-col gap-3">
          <Button
            asChild
            size="lg"
            className="w-full bg-white text-zinc-900 hover:bg-zinc-100"
          >
            <Link href={`${API}/auth/google`}>
              <GoogleIcon />
              Sign in with Google
            </Link>
          </Button>

          <Button
            asChild
            size="lg"
            variant="outline"
            className="w-full border-[#5865F2]/40 bg-[#5865F2]/10 text-zinc-100 hover:bg-[#5865F2]/20 hover:border-[#5865F2]/60"
          >
            <Link href={`${API}/auth/discord`}>
              <DiscordIcon />
              Sign in with Discord
            </Link>
          </Button>
          </div>
        )}

        <p className="mt-6 text-center text-xs text-zinc-500">
          Access is restricted to pre-registered members.
        </p>
      </div>
    </main>
  )
}

function GoogleIcon() {
  return (
    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  )
}

function DiscordIcon() {
  return (
    <svg className="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057c.002.022.015.042.033.053a19.89 19.89 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03z" />
    </svg>
  )
}
