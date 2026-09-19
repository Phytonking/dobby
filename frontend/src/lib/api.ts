import type { User, Integration, GuildSettings, AuditEntry, Contact } from './types'

// Always use same-origin /api proxy — avoids cross-port cookie issues.
// next.config.ts rewrites /api/* → dashboard:8000/*.
export const API = typeof window === 'undefined'
  ? (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000')
  : '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error((err as { detail?: string }).detail ?? `Request failed: ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  me: () => request<User>('/me'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),

  integrations: {
    list: () => request<Integration[]>('/integrations'),
    connectUrl: (provider: string) => `/api/integrations/${provider}/connect`,
    disconnect: (provider: string) => request<void>(`/integrations/${provider}`, { method: 'DELETE' }),
  },

  admin: {
    users: {
      list: () => request<User[]>('/admin/users'),
      create: (data: { uw_email: string; discord_id?: string; display_name: string; role: string }) =>
        request<User>('/admin/users', { method: 'POST', body: JSON.stringify(data) }),
      remove: (id: string) => request<void>(`/admin/users/${id}`, { method: 'DELETE' }),
      setRole: (id: string, role: string) =>
        request<User>(`/admin/users/${id}/role`, { method: 'PATCH', body: JSON.stringify({ role }) }),
    },
    settings: {
      get: (guildId: string) => request<GuildSettings>(`/admin/settings/${guildId}`),
      update: (guildId: string, data: Partial<GuildSettings>) =>
        request<GuildSettings>(`/admin/settings/${guildId}`, { method: 'PUT', body: JSON.stringify(data) }),
    },
    audit: (params?: { limit?: number; offset?: number; tool?: string; status?: string }) => {
      const q = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params ?? {}).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
        )
      ).toString()
      return request<AuditEntry[]>(`/admin/audit${q ? '?' + q : ''}`)
    },
    contacts: (guildId: string) => request<Contact[]>(`/admin/contacts/${guildId}`),
  },
}

