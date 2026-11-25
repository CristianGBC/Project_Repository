import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { isAxiosError } from 'axios'
import { Toaster } from 'sonner'
import { createSession } from './api/session'
import { makeRouter } from './routes/router'
import './styles.css'

const query = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30000, gcTime: 300000, refetchOnWindowFocus: false,
      retry: (count, error) => {
        const status = isAxiosError(error) ? error.response?.status : undefined
        return count < 1 && (!status || status >= 500)
      },
    },
    mutations: { retry: false },
  },
})
const router = makeRouter(createSession(query))
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={query}><RouterProvider router={router} /><Toaster /></QueryClientProvider>,
)
