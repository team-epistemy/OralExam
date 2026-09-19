import { QueryClient } from '@tanstack/react-query'

// Idle data stays cached for staleTime; freshness after a write is action-driven —
// each mutation invalidateQueries() the keys it changed, which refetches active
// queries regardless of staleTime. So don't rely on staleTime for post-action UI.
export const queryClientOptions = {
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
} as const

export const createQueryClient = () => new QueryClient(queryClientOptions)
