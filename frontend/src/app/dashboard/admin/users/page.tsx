'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { UserPlus, Trash2, ShieldCheck, ShieldOff } from 'lucide-react'
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
import type { User } from '@/lib/types'

interface NewUserForm {
  display_name: string
  uw_email: string
  discord_id: string
  role: 'student' | 'admin'
}

function AddUserDialog({ onSuccess }: { onSuccess: () => void }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<NewUserForm>({
    display_name: '',
    uw_email: '',
    discord_id: '',
    role: 'student',
  })

  const mutation = useMutation({
    mutationFn: () =>
      api.admin.users.create({
        display_name: form.display_name,
        uw_email: form.uw_email,
        discord_id: form.discord_id || undefined,
        role: form.role,
      }),
    onSuccess: () => {
      setOpen(false)
      setForm({ display_name: '', uw_email: '', discord_id: '', role: 'student' })
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
            Add a pre-registered UW student or admin to the bot roster.
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
            <Label htmlFor="uw_email">UW email</Label>
            <Input
              id="uw_email"
              type="email"
              value={form.uw_email}
              onChange={(e) => setForm((f) => ({ ...f, uw_email: e.target.value }))}
              placeholder="jane@uw.edu"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="discord_id">Discord ID (optional)</Label>
            <Input
              id="discord_id"
              value={form.discord_id}
              onChange={(e) => setForm((f) => ({ ...f, discord_id: e.target.value }))}
              placeholder="123456789012345678"
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
          <p className="text-sm text-red-400">{(mutation.error as Error).message}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending || !form.display_name || !form.uw_email}
          >
            {mutation.isPending ? 'Adding…' : 'Add user'}
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
  isActing,
}: {
  user: User
  onRemove: (id: string) => void
  onToggleRole: (id: string, role: string) => void
  isActing: boolean
}) {
  return (
    <TableRow>
      <TableCell className="font-medium text-zinc-100">{user.display_name}</TableCell>
      <TableCell className="text-zinc-300">{user.uw_email ?? '—'}</TableCell>
      <TableCell className="font-mono text-xs text-zinc-400">{user.discord_id ?? '—'}</TableCell>
      <TableCell>
        <Badge variant={user.role === 'admin' ? 'success' : 'secondary'} className="capitalize">
          {user.role}
        </Badge>
      </TableCell>
      <TableCell className="text-zinc-400 text-xs">
        {new Date(user.created_at).toLocaleDateString('en-US', {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
        })}
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            title={user.role === 'admin' ? 'Demote to student' : 'Promote to admin'}
            onClick={() => onToggleRole(user.id, user.role === 'admin' ? 'student' : 'admin')}
            disabled={isActing}
          >
            {user.role === 'admin' ? (
              <ShieldOff className="h-4 w-4 text-zinc-400" />
            ) : (
              <ShieldCheck className="h-4 w-4 text-zinc-400" />
            )}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title="Remove user"
            onClick={() => onRemove(user.id)}
            disabled={isActing}
          >
            <Trash2 className="h-4 w-4 text-red-400" />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  )
}

export default function UsersPage() {
  const queryClient = useQueryClient()

  const { data: users, isLoading } = useQuery({
    queryKey: ['admin', 'users'],
    queryFn: api.admin.users.list,
  })

  const removeMutation = useMutation({
    mutationFn: (id: string) => api.admin.users.remove(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })

  const roleMutation = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) => api.admin.users.setRole(id, role),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] }),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100">Users</h1>
          <p className="mt-1 text-sm text-zinc-400">
            Manage the allowlist of students and admins.
          </p>
        </div>
        <AddUserDialog
          onSuccess={() => queryClient.invalidateQueries({ queryKey: ['admin', 'users'] })}
        />
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
                  <TableHead>UW Email</TableHead>
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
                      isActing={
                        (removeMutation.isPending && removeMutation.variables === user.id) ||
                        (roleMutation.isPending && roleMutation.variables?.id === user.id)
                      }
                    />
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={6} className="py-10 text-center text-zinc-500">
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
