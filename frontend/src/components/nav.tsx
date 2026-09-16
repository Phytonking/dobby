'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import {
  Home,
  Plug,
  Users,
  Settings,
  ClipboardList,
  BookUser,
  LogOut,
  Bot,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { User } from '@/lib/types'

interface NavProps {
  user: User
}

const studentLinks = [
  { href: '/dashboard', label: 'Home', icon: Home },
  { href: '/dashboard/integrations', label: 'Integrations', icon: Plug },
  { href: '/dashboard/contacts', label: 'Contacts', icon: BookUser },
]

const adminLinks = [
  { href: '/dashboard/admin/users', label: 'Users', icon: Users },
  { href: '/dashboard/admin/settings', label: 'Guild Settings', icon: Settings },
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
    <aside className="fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-zinc-800 bg-zinc-900">
      {/* Logo */}
      <div className="flex h-16 items-center gap-2 border-b border-zinc-800 px-5">
        <Bot className="h-6 w-6 text-indigo-400" />
        <span className="text-lg font-semibold text-zinc-100">Dobby</span>
      </div>

      {/* User info */}
      <div className="border-b border-zinc-800 px-5 py-4">
        <p className="truncate text-sm font-medium text-zinc-100">{user.display_name}</p>
        <p className="truncate text-xs text-zinc-400">{user.uw_email ?? user.discord_id ?? ''}</p>
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
                    ? 'bg-zinc-800 text-zinc-100'
                    : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
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
                <p className="px-3 pb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
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
                        ? 'bg-zinc-800 text-zinc-100'
                        : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
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
      <div className="border-t border-zinc-800 p-3">
        <Button
          variant="ghost"
          className="w-full justify-start gap-3 text-zinc-400 hover:text-zinc-100"
          onClick={handleLogout}
        >
          <LogOut className="h-4 w-4" />
          Sign out
        </Button>
      </div>
    </aside>
  )
}
