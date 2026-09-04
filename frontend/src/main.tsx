import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SSEProvider } from './components/SSEProvider'
import { router } from './router'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Keep views fresh: treat data as immediately stale so navigating to a
      // tab/page (or refocusing the window) refetches, and any create/edit/
      // delete/update shows up promptly across views — not up to 30s later.
      // Mutations still invalidate their keys for instant same-view updates;
      // this is the cross-view safety net.
      staleTime: 0,
      refetchOnMount: 'always',
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SSEProvider>
        <RouterProvider router={router} />
      </SSEProvider>
    </QueryClientProvider>
  </StrictMode>,
)
