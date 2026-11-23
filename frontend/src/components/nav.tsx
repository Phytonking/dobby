'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import {
  Home,
  Plug,
  Users,
  ClipboardList,
  LogOut,
  Bot,
  HeartPulse,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { User } from '@/lib/types'

interface NavProps {
  user: User
}

const studentLinks = [{ href: '/dashboard', label: 'Home', icon: Home }]

const adminLinks = [
  { href: '/dashboard/admin/health', label: 'System health', icon: HeartPulse },
  { href: '/dashboard/admin/users', label: 'Users', icon: Users },
  { href: '/dashboard/integrations', label: 'Service accounts', icon: Plug },
  { href: '/dashboard/admin/audit', label: 'Audit Log', icon: ClipboardList },
]

export function Nav({ user }: NavProps) {
  const pathname = usePathname()
  const router = useRouter()

  async function handleLogout() {
    try {
      await api.logout()
    } catch {
      // ignore logout errors
    }
    router.push('/login')
  }

  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-border bg-card">
      {/* Logo */}
      <div className="flex h-16 items-center gap-2 border-b border-border px-5">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Bot className="h-5 w-5" />
        </span>
        <span className="text-lg font-semibold text-foreground">Dobby</span>
      </div>

      {/* User info */}
      <div className="border-b border-border px-5 py-4">
        <p className="truncate text-sm font-medium text-foreground">{user.display_name}</p>
        <p className="truncate text-xs text-muted-foreground">{user.uw_email ?? user.discord_id ?? ''}</p>
        <div className="mt-2">
          <Badge variant={user.role === 'admin' ? 'success' : 'secondary'} className="capitalize">
            {user.role}
          </Badge>
        </div>
      </div>

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1">
          {studentLinks.map(({ href, label, icon: Icon }) => (
            <li key={href}>
              <Link
                href={href}
                className={cn(
                  'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                  pathname === href
                    ? 'bg-accent text-accent-foreground'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {label}
              </Link>
            </li>
          ))}

          {user.role === 'admin' && (
            <>
              <li className="pt-4">
                <p className="px-3 pb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Admin
                </p>
              </li>
              {adminLinks.map(({ href, label, icon: Icon }) => (
                <li key={href}>
                  <Link
                    href={href}
                    className={cn(
                      'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
                      pathname === href
                        ? 'bg-accent text-accent-foreground'
                        : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                    )}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    {label}
                  </Link>
                </li>
              ))}
            </>
          )}
        </ul>
      </nav>

      {/* Logout */}
      <div className="border-t border-border p-3">
        <Button
          variant="ghost"
          className="w-full justify-start gap-3 text-muted-foreground hover:text-foreground"
          onClick={handleLogout}
        >
          <LogOut className="h-4 w-4" />
          Sign out
        </Button>
      </div>
    </aside>
  )
}
