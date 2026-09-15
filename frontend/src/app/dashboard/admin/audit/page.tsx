'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { api } from '@/lib/api'

const PAGE_SIZE = 25

type StatusVariant = 'success' | 'error' | 'warning' | 'secondary'

function statusVariant(status: string): StatusVariant {
  if (status === 'success') return 'success'
  if (status === 'error' || status === 'failed') return 'error'
  if (status === 'pending') return 'warning'
  return 'secondary'
}

export default function AuditPage() {
  const [page, setPage] = useState(0)
  const [toolFilter, setToolFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')

  const { data: entries, isLoading } = useQuery({
    queryKey: ['admin', 'audit', page, toolFilter, statusFilter],
    queryFn: () =>
      api.admin.audit({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        tool: toolFilter || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
      }),
  })

  function handlePrev() {
    setPage((p) => Math.max(0, p - 1))
  }

  function handleNext() {
    if (entries && entries.length === PAGE_SIZE) setPage((p) => p + 1)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-zinc-100">Audit Log</h1>
        <p className="mt-1 text-sm text-zinc-400">
          A record of all tool calls and bot actions.
        </p>
      </div>

      <Card>
        <CardHeader className="pb-4">
          <div className="flex flex-wrap items-center gap-4">
            <CardTitle className="text-base">Events</CardTitle>
            <div className="flex flex-1 flex-wrap items-center gap-3">
              <Input
                placeholder="Filter by tool name…"
                value={toolFilter}
                onChange={(e) => {
                  setToolFilter(e.target.value)
                  setPage(0)
                }}
                className="w-48"
              />
              <Select
                value={statusFilter}
                onValueChange={(v) => {
                  setStatusFilter(v)
                  setPage(0)
                }}
              >
                <SelectTrigger className="w-36">
                  <SelectValue placeholder="All statuses" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  <SelectItem value="success">Success</SelectItem>
                  <SelectItem value="error">Error</SelectItem>
                  <SelectItem value="pending">Pending</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="space-y-2 p-6">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Timestamp</TableHead>
                    <TableHead>Discord ID</TableHead>
                    <TableHead>Tool</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead>Summary</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries && entries.length > 0 ? (
                    entries.map((entry) => (
                      <TableRow key={entry.id}>
                        <TableCell className="text-xs text-zinc-400 whitespace-nowrap">
                          {new Date(entry.created_at).toLocaleString('en-US', {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                            second: '2-digit',
                          })}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-zinc-400">
                          {entry.discord_id ?? '—'}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-zinc-200">
                          {entry.tool ?? '—'}
                        </TableCell>
                        <TableCell>
                          <Badge variant={statusVariant(entry.status)} className="capitalize">
                            {entry.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-zinc-400">
                          {entry.duration_ms != null ? `${entry.duration_ms}ms` : '—'}
                        </TableCell>
                        <TableCell className="max-w-xs truncate text-xs text-zinc-400">
                          {entry.input_summary ?? '—'}
                        </TableCell>
                      </TableRow>
                    ))
                  ) : (
                    <TableRow>
                      <TableCell colSpan={6} className="py-10 text-center text-zinc-500">
                        No audit entries found.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>

              {/* Pagination */}
              <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-3">
                <p className="text-xs text-zinc-400">
                  Page {page + 1}
                </p>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={handlePrev} disabled={page === 0}>
                    <ChevronLeft className="h-4 w-4" />
                    Prev
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleNext}
                    disabled={!entries || entries.length < PAGE_SIZE}
                  >
                    Next
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
