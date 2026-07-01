import { QueryClient } from '@tanstack/react-query'

/**
 * Single app-wide QueryClient (module scope, like the router in App.jsx).
 *
 * Defaults:
 *  - retry: 1            — one retry on failure; API errors surface quickly
 *  - refetchOnWindowFocus: false — WS invalidations keep data fresh instead
 *  - staleTime: 30s      — avoid refetch storms when remounting/navigating;
 *                          mutations + WS events invalidate explicitly
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
})
