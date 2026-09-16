'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BookUser } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api } from '@/lib/api'

const GUILD_ID = process.env.NEXT_PUBLIC_GUILD_ID ?? ''

export default function ContactsPage() {
  const [search, setSearch] = useState('')

  const { data: contacts, isLoading } = useQuery({
    queryKey: ['contacts', GUILD_ID],
    queryFn: () => api.admin.contacts(GUILD_ID),
    enabled: !!GUILD_ID,
  })

  const filtered = contacts?.filter(
    (c) =>
      c.display_name.toLowerCase().includes(search.toLowerCase()) ||
      c.email.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-100">Contacts</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Guild contacts managed by Dobby. Read-only — contacts are added via bot commands.
        </p>
      </div>

      <Card>
        <CardHeader className="pb-4">
          <div className="flex items-center justify-between gap-4">
            <CardTitle className="text-base">All contacts</CardTitle>
            <Input
              placeholder="Search by name or email…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-64"
            />
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-6">
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Added by</TableHead>
                  <TableHead>Date added</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered && filtered.length > 0 ? (
                  filtered.map((contact) => (
                    <TableRow key={contact.name_key}>
                      <TableCell className="font-medium text-zinc-100">
                        {contact.display_name}
                      </TableCell>
                      <TableCell className="text-zinc-300">{contact.email}</TableCell>
                      <TableCell className="text-zinc-400">
                        {contact.added_by ?? <span className="italic text-zinc-600">unknown</span>}
                      </TableCell>
                      <TableCell className="text-zinc-400">
                        {new Date(contact.created_at).toLocaleDateString('en-US', {
                          year: 'numeric',
                          month: 'short',
                          day: 'numeric',
                        })}
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={4} className="py-10 text-center text-zinc-500">
                      <BookUser className="mx-auto mb-2 h-6 w-6 text-zinc-700" />
                      {search ? 'No contacts match your search.' : 'No contacts yet.'}
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
