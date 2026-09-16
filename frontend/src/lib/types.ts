export interface User {
  id: string
  uw_email: string | null
  discord_id: string | null
  display_name: string
  role: 'admin' | 'student'
  created_at: string
}

export interface Integration {
  provider: string
  connected_at: string
}

export interface GuildSettings {
  guild_id: string
  timezone: string
  model: string
  allowed_role_ids: string[]
  admin_role_ids: string[]
  allowed_channel_ids: string[]
  mention_channel_ids: string[]
  context_limit: number
}

export interface AuditEntry {
  id: number
  discord_id: string | null
  tool: string | null
  status: string
  duration_ms: number | null
  created_at: string
  input_summary: string | null
}

export interface Contact {
  name_key: string
  display_name: string
  email: string
  added_by: string | null
  created_at: string
}
