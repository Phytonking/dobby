'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { UserPlus, Trash2, ShieldCheck, ShieldOff, Pencil, Upload } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api } from '@/lib/api'
import type { ImportResult, User } from '@/lib/types'

interface NewUserForm {
  display_name: string
  uw_email: string
  discord_id: string
  calendar_email: string
  role: 'student' | 'admin'
}

const EMPTY_FORM: NewUserForm = {
  display_name: '',
  uw_email: '',
  discord_id: '',
  calendar_email: '',
  role: 'student',
}

function AddUserDialog({ onSuccess }: { onSuccess: () => void }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<NewUserForm>(EMPTY_FORM)

  const mutation = useMutation({
    mutationFn: () =>
      api.admin.users.create({
        display_name: form.display_name,
        uw_email: form.uw_email.trim() || undefined,
        discord_id: form.discord_id || undefined,
        calendar_email: form.calendar_email || undefined,
        role: form.role,
      }),
    onSuccess: () => {
      setOpen(false)
      setForm(EMPTY_FORM)
      onSuccess()
    },
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" className="gap-2">
          <UserPlus className="h-4 w-4" />
          Add user
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add user to allowlist</DialogTitle>
          <DialogDescription>
            Add a student or admin to the roster. Give a Google email, a Discord ID, or both —
            whichever they sign in or talk to Dobby with.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="display_name">Display name</Label>
            <Input
              id="display_name"
              value={form.display_name}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
              placeholder="Jane Doe"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="uw_email">Google email (for Google sign-in)</Label>
            <Input
              id="uw_email"
              type="email"
              value={form.uw_email}
              onChange={(e) => setForm((f) => ({ ...f, uw_email: e.target.value }))}
              placeholder="jane@gmail.com"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="discord_id">Discord ID</Label>
            <Input
              id="discord_id"
              value={form.discord_id}
              onChange={(e) => setForm((f) => ({ ...f, discord_id: e.target.value }))}
              placeholder="123456789012345678"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="calendar_email">Calendar email (optional)</Label>
            <Input
              id="calendar_email"
              type="email"
              value={form.calendar_email}
              onChange={(e) => setForm((f) => ({ ...f, calendar_email: e.target.value }))}
              placeholder="Where Dobby sends meeting invitations"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="role">Role</Label>
            <Select
              value={form.role}
              onValueChange={(v) => setForm((f) => ({ ...f, role: v as 'student' | 'admin' }))}
            >
              <SelectTrigger id="role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="student">Student</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        {mutation.isError && (
          <p className="text-sm text-red-600">{(mutation.error as Error).message}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={
              mutation.isPending || !form.display_name.trim() || !(form.uw_email.trim() || form.discord_id.trim())
            }
          >
            {mutation.isPending ? 'Adding…' : 'Add user'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

const SAMPLE_CSV = `name,email,discord_id,calendar_email,role
Jane Doe,jane@gmail.com,,,student
Sam Lee,,123456789012345678,sam@uw.edu,student`

const OUTCOME_STYLE: Record<string, { label: string; variant: 'success' | 'secondary' | 'error' }> = {
  created: { label: 'Added', variant: 'success' },
  would_create: { label: 'Will add', variant: 'success' },
  skipped: { label: 'Skipped', variant: 'secondary' },
  invalid: { label: 'Problem', variant: 'error' },
}

function ImportUsersDialog({ onSuccess }: { onSuccess: () => void }) {
  const [open, setOpen] = useState(false)
  const [csv, setCsv] = useState('')
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const [done, setDone] = useState<ImportResult | null>(null)

  const reset = () => {
    setCsv('')
    setPreview(null)
    setDone(null)
  }

  const previewMutation = useMutation({
    mutationFn: () => api.admin.users.import(csv, true),
    onSuccess: setPreview,
  })
  const importMutation = useMutation({
    mutationFn: () => api.admin.users.import(csv, false),
    onSuccess: (result) => {
      setDone(result)
      onSuccess()
    },
  })

  const result = done ?? preview
  const error = (previewMutation.error ?? importMutation.error) as Error | null

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reset()
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" variant="outline" className="gap-2">
          <Upload className="h-4 w-4" />
          Import CSV
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import teammates</DialogTitle>
          <DialogDescription>
            Upload or paste a CSV with a <strong>name</strong> column and an <strong>email</strong> or{' '}
            <strong>discord_id</strong> column. Optional: calendar_email, role. A Google Contacts export
            works too. People already on the roster are skipped, never changed.
          </DialogDescription>
        </DialogHeader>
        {!done && (
          <div className="grid gap-3">
            <div className="grid gap-2">
              <Label htmlFor="import_file">CSV file</Label>
              <Input
                id="import_file"
                type="file"
                accept=".csv,text/csv"
                onChange={async (e) => {
                  const file = e.target.files?.[0]
                  if (file) {
                    setCsv(await file.text())
                    setPreview(null)
                  }
                }}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="import_csv">…or paste it</Label>
              <textarea
                id="import_csv"
                value={csv}
                onChange={(e) => {
                  setCsv(e.target.value)
                  setPreview(null)
                }}
                rows={6}
                placeholder={SAMPLE_CSV}
                className="w-full rounded-md border border-input bg-card px-3 py-2 font-mono text-xs text-foreground shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
            </div>
          </div>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error.message}
          </p>
        )}
        {result && (
          <div className="space-y-3">
            <p role="status" className="text-sm text-foreground">
              {done
                ? `Added ${done.created} ${done.created === 1 ? 'person' : 'people'}.`
                : `${result.created} to add.`}{' '}
              {result.skipped > 0 && `${result.skipped} skipped. `}
              {result.invalid > 0 && `${result.invalid} with problems${done ? ' were not added' : ' will be left out'}.`}
            </p>
            <div className="max-h-64 overflow-y-auto rounded-md border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Line</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Result</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {result.rows.map((row) => (
                    <TableRow key={row.line}>
                      <TableCell className="text-muted-foreground">{row.line}</TableCell>
                      <TableCell>{row.display_name ?? '—'}</TableCell>
                      <TableCell>
                        <Badge variant={OUTCOME_STYLE[row.outcome].variant}>{OUTCOME_STYLE[row.outcome].label}</Badge>
                        {row.reason && <span className="ml-2 text-xs text-muted-foreground">{row.reason}</span>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        )}
        <DialogFooter>
          {done ? (
            <Button onClick={() => setOpen(false)}>Done</Button>
          ) : preview ? (
            <>
              <Button variant="outline" onClick={() => setPreview(null)}>
                Back
              </Button>
              <Button
                onClick={() => importMutation.mutate()}
                disabled={importMutation.isPending || preview.created === 0}
              >
                {importMutation.isPending ? 'Importing…' : `Import ${preview.created}`}
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button onClick={() => previewMutation.mutate()} disabled={!csv.trim() || previewMutation.isPending}>
                {previewMutation.isPending ? 'Checking…' : 'Preview'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditUserDialog({ user, onSuccess }: { user: User; onSuccess: () => void }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({
    display_name: user.display_name,
    discord_id: user.discord_id ?? '',
    calendar_email: user.calendar_email ?? '',
  })

  const mutation = useMutation({
    mutationFn: () =>
      api.admin.users.update(user.id, {
        display_name: form.display_name,
        discord_id: form.discord_id || null,
        calendar_email: form.calendar_email || null,
      }),
    onSuccess: () => {
      setOpen(false)
      onSuccess()
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) {
          setForm({
            display_name: user.display_name,
            discord_id: user.discord_id ?? '',
            calendar_email: user.calendar_email ?? '',
          })
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" title="Edit user">
          <Pencil className="h-4 w-4 text-muted-foreground" />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit {user.display_name}</DialogTitle>
          <DialogDescription>
            The calendar email is where Dobby sends this person&apos;s meeting invitations.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor={`edit_name_${user.id}`}>Display name</Label>
            <Input
              id={`edit_name_${user.id}`}
              value={form.display_name}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor={`edit_discord_${user.id}`}>Discord ID</Label>
            <Input
              id={`edit_discord_${user.id}`}
              value={form.discord_id}
              onChange={(e) => setForm((f) => ({ ...f, discord_id: e.target.value }))}
              placeholder="123456789012345678"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor={`edit_email_${user.id}`}>Calendar email</Label>
            <Input
              id={`edit_email_${user.id}`}
              type="email"
              value={form.calendar_email}
              onChange={(e) => setForm((f) => ({ ...f, calendar_email: e.target.value }))}
              placeholder="jane@gmail.com"
            />
          </div>
        </div>
        {mutation.isError && (
          <p className="text-sm text-red-600">{(mutation.error as Error).message}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={() => mutation.mutate()} disabled={mutation.isPending || !form.display_name}>
            {mutation.isPending ? 'Saving…' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function UserRow({
  user,
  onRemove,
  onToggleRole,
  onEdited,
  isActing,
}: {
  user: User
  onRemove: (id: string) => void
  onToggleRole: (id: string, role: string) => void
  onEdited: () => void
  isActing: boolean
}) {
  return (
    <TableRow>
      <TableCell className="font-medium text-foreground">{user.display_name}</TableCell>
      <TableCell className="text-foreground/80">{user.uw_email ?? '—'}</TableCell>
      <TableCell className="text-foreground/80">
        {user.calendar_email ?? <span className="italic text-muted-foreground">not set</span>}
      </TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">{user.discord_id ?? '—'}</TableCell>
      <TableCell>
        <Badge variant={user.role === 'admin' ? 'success' : 'secondary'} className="capitalize">
          {user.role}
        </Badge>
      </TableCell>
      <TableCell className="text-muted-foreground text-xs">
        {new Date(user.created_at).toLocaleDateString('en-US', {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
        })}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <EditUserDialog user={user} onSuccess={onEdited} />
          <Button
            variant="ghost"
            size="sm"
            title={user.role === 'admin' ? 'Demote to student' : 'Promote to admin'}
            onClick={() => onToggleRole(user.id, user.role === 'admin' ? 'student' : 'admin')}
            disabled={isActing}
          >
            {user.role === 'admin' ? (
              <ShieldOff className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ShieldCheck className="h-4 w-4 text-muted-foreground" />
            )}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title="Remove user"
            onClick={() => onRemove(user.id)}
            disabled={isActing}
          >
            <Trash2 className="h-4 w-4 text-red-600" />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  )
}

export default function UsersPage() {
  const queryClient = useQueryClient()
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })

  const { data: users, isLoading } = useQuery({
    queryKey: ['admin', 'users'],
    queryFn: api.admin.users.list,
  })

  const removeMutation = useMutation({
    mutationFn: (id: string) => api.admin.users.remove(id),
    onSuccess: refresh,
  })

  const roleMutation = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) => api.admin.users.setRole(id, role),
    onSuccess: refresh,
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Users</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage the allowlist of students and admins, and the calendar emails Dobby invites.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ImportUsersDialog onSuccess={refresh} />
          <AddUserDialog onSuccess={refresh} />
        </div>
      </div>

      <Card>
        <CardHeader className="pb-4">
          <CardTitle className="text-base">
            {users ? `${users.length} user${users.length !== 1 ? 's' : ''}` : 'Users'}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-6">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Google email</TableHead>
                  <TableHead>Calendar email</TableHead>
                  <TableHead>Discord ID</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Added</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users && users.length > 0 ? (
                  users.map((user) => (
                    <UserRow
                      key={user.id}
                      user={user}
                      onRemove={(id) => removeMutation.mutate(id)}
                      onToggleRole={(id, role) => roleMutation.mutate({ id, role })}
                      onEdited={refresh}
                      isActing={
                        (removeMutation.isPending && removeMutation.variables === user.id) ||
                        (roleMutation.isPending && roleMutation.variables?.id === user.id)
                      }
                    />
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                      No users in the allowlist.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
