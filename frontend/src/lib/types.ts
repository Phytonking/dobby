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

export interface ImportRow {
  line: number
  display_name: string | null
  outcome: 'created' | 'would_create' | 'skipped' | 'invalid'
  reason: string | null
}

export interface ImportResult {
  created: number
  skipped: number
  invalid: number
  rows: ImportRow[]
}

export interface ServiceHealth {
  key: string
  connection: IntegrationStatus | null
  calls: number
  failures: number
  last_success_at: string | null
  last_failure_at: string | null
}

export interface HealthReport {
  window_hours: number
  generated_at: string
  composio_verified: boolean
  calls: number
  failures: number
  last_activity_at: string | null
  services: ServiceHealth[]
  hourly: { hour: string; ok: number; failed: number }[]
}
