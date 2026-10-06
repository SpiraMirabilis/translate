import { useQuery } from '@tanstack/react-query'
import { publicApi } from '../services/api'

/**
 * Is reader error reporting switched on? (Settings → Reader error reports.)
 *
 * One shared query key, so the Reader and the book page fetch this once between
 * them per session rather than per mount. Defaults to `true` while in flight and
 * on error: the server refuses the POST anyway, and hiding the button on a
 * transient network blip would be the worse failure.
 */
export function useErrorReportsEnabled() {
  const { data } = useQuery({
    queryKey: ['error-reports-enabled'],
    queryFn: () => publicApi.getErrorReportsEnabled(),
    staleTime: 10 * 60 * 1000,
    retry: false,
  })
  return data?.enabled !== false
}
