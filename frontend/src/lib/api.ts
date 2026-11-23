import type { User, Integration, AuditEntry, Paginated, ImportResult, HealthReport } from './types'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface ValidationIssue {
  loc?: (string | number)[]
  msg?: string
}

// FastAPI sends a string for handled errors and a list of issues for validation (422) errors.
export function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return (detail as ValidationIssue[])
      .map((issue) => {
        const field = issue.loc?.filter((part) => part !== 'body').join('.')
        const msg = (issue.msg ?? 'is invalid').replace(/^Value error, /, '')
        return field ? `${field.replace(/_/g, ' ')}: ${msg}` : msg
      })
      .join('; ')
  }
  return `Request failed: ${status}`
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...options,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(errorMessage((err as { detail?: unknown }).detail, res.status))
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export interface UserEdit {
  display_name?: string
  discord_id?: string | null
  calendar_email?: string | null
}

export const api = {
  loginMethods: () => request<{ local: boolean; oauth: boolean }>('/auth/methods'),
  localLogin: (username: string, password: string) => request<{ ok: boolean }>('/auth/local', {
    method: 'POST', body: JSON.stringify({ username, password }),
  }),
  me: () => request<User>('/me'),
  updateMe: (data: { calendar_email?: string | null; display_name?: string }) =>
    request<User>('/me', { method: 'PATCH', body: JSON.stringify(data) }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),

  // Dobby's service accounts — admin only.
  integrations: {
    list: () => request<Integration[]>('/integrations'),
    connectUrl: (provider: string) => `${API}/integrations/${provider}/connect`,
    disconnect: (provider: string) => request<void>(`/integrations/${provider}`, { method: 'DELETE' }),
  },

  admin: {
    users: {
      list: () => request<Paginated<User>>('/admin/users?limit=200').then((page) => page.items),
      create: (data: {
        uw_email?: string
        discord_id?: string
        display_name: string
        calendar_email?: string
        role: string
      }) => request<User>('/admin/users', { method: 'POST', body: JSON.stringify(data) }),
      update: (id: string, data: UserEdit) =>
        request<User>(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
      import: (csv: string, dryRun: boolean) =>
        request<ImportResult>('/admin/users/import', {
          method: 'POST',
          body: JSON.stringify({ csv, dry_run: dryRun }),
        }),
      remove: (id: string) => request<void>(`/admin/users/${id}`, { method: 'DELETE' }),
      setRole: (id: string, role: string) =>
        request<User>(`/admin/users/${id}/role`, { method: 'PATCH', body: JSON.stringify({ role }) }),
    },
    health: () => request<HealthReport>('/admin/health'),
    audit: (params?: { limit?: number; offset?: number; tool?: string; status?: string }) => {
      const q = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params ?? {}).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
        )
      ).toString()
      return request<Paginated<AuditEntry>>(`/admin/audit${q ? '?' + q : ''}`).then((page) => page.items)
    },
  },
}

export { API }
