import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-zinc-100 text-zinc-900',
        secondary: 'border-transparent bg-zinc-700 text-zinc-200',
        destructive: 'border-transparent bg-red-700 text-zinc-100',
        outline: 'text-zinc-100 border-zinc-600',
        success: 'border-transparent bg-emerald-700 text-emerald-100',
        warning: 'border-transparent bg-amber-700 text-amber-100',
        error: 'border-transparent bg-red-700 text-red-100',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
