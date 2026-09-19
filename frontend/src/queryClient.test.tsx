import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { QueryClientProvider, useQuery } from '@tanstack/react-query'
import { createQueryClient } from './queryClient'

// The app is action-driven: a mutation invalidates the keys it changed and the
// mounted query refetches — even under the non-zero default staleTime. This guards
// that mechanism so "did an action -> UI updates" keeps working.
function Probe({ fetchSpy }: { fetchSpy: () => Promise<string> }) {
  useQuery({ queryKey: ['probe'], queryFn: fetchSpy })
  return null
}

describe('app query defaults', () => {
  it('invalidateQueries refetches an active query despite staleTime', async () => {
    const client = createQueryClient()
    const fetchSpy = vi.fn().mockResolvedValue('ok')

    render(
      <QueryClientProvider client={client}>
        <Probe fetchSpy={fetchSpy} />
      </QueryClientProvider>,
    )
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))

    // Simulate a mutation's onSuccess invalidating the data it changed.
    await client.invalidateQueries({ queryKey: ['probe'] })
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2))
  })
})
