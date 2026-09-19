import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { Nav } from '@/components/nav'
import type { User } from '@/lib/types'

const INTERNAL_API = process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

async function getMe(cookieHeader: string): Promise<User | null> {
  try {
    const res = await fetch(`${INTERNAL_API}/me`, {
      headers: { Cookie: cookieHeader },
      cache: 'no-store',
    })
    if (!res.ok) return null
    return res.json() as Promise<User>
  } catch {
    return null
  }
}

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies()
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join('; ')

  const user = await getMe(cookieHeader)

  if (!user) {
    redirect('/login')
  }

  return (
    <div className="min-h-screen bg-zinc-950">
      <Nav user={user} />
      <div className="pl-60">
        <main className="min-h-screen p-8">{children}</main>
      </div>
    </div>
  )
}
