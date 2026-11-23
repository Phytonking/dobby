export interface User {
  id: string
  uw_email: string | null
  discord_id: string | null
  display_name: string
  calendar_email: string | null
  role: 'admin' | 'student'
  created_at: string
}

export type IntegrationStatus = 'connected' | 'pending' | 'needs_reconnect' | 'disconnected'

export interface Integration {
  provider: string
  status: IntegrationStatus
  // False when Composio couldn't be reached and the status is only the dashboard's record.
  verified: boolean
  connected_by: string | null
  connected_at: string | null
}

export interface AuditEntry {
  id: number
  discord_id: string | null
  tool: string | null
  status: string
  duration_ms: number | null
  created_at: string
}

export interface Paginated<T> {
  total: number
  items: T[]
}
