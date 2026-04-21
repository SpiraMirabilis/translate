import { useCallback, useMemo } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'

// useUrlState(key, defaultValue, opts)
//
// Syncs a single piece of UI state with a query-string param. Returns
// [value, setValue] like useState.
//
//   opts.push         - true = add history entry, false (default) = replace
//   opts.serialize    - (v) => string, default String
//   opts.deserialize  - (s) => value, default identity
//
// Setting the value to null, '', undefined, or the defaultValue removes the
// param. Always snapshots the URLSearchParams before mutating so concurrent
// setters don't clobber each other.
export function useUrlState(key, defaultValue = '', opts = {}) {
  const { push = false, serialize = String, deserialize = (s) => s } = opts
  const [sp, setSp] = useSearchParams()
  const raw = sp.get(key)
  const value = raw == null ? defaultValue : deserialize(raw)

  const setValue = useCallback((next) => {
    setSp((prev) => {
      const current = prev.get(key)
      const currentVal = current == null ? defaultValue : deserialize(current)
      const resolved = typeof next === 'function' ? next(currentVal) : next
      const out = new URLSearchParams(prev)
      if (resolved == null || resolved === '' || resolved === defaultValue) {
        out.delete(key)
      } else {
        out.set(key, serialize(resolved))
      }
      return out
    }, { replace: !push })
  }, [setSp, key, defaultValue, serialize, deserialize, push])

  return [value, setValue]
}

// useUrlModal(name, opts)
//
// Backs a named modal with the URL. Exactly one modal is open at a time
// (the `modal` query param). Opening pushes a history entry so the browser
// back button closes the modal.
//
//   opts.paramKeys - list of additional query-param keys "owned" by this
//                    modal. These get set on open(payload) and cleared on
//                    close(). Use for modals that need a payload id
//                    (e.g. ['book', 'ch']).
//
// Returns { isOpen, params, open(payload?), close() }.
//   - params: object with the current values of paramKeys when open
//   - open(payload): payload is a primitive (for single-key modals) or an
//                    object { key: value } for multi-key. Primitives set
//                    paramKeys[0] if present.
//   - close(): navigate(-1) when there's history to pop; otherwise clears
//              modal + paramKeys via replace.
export function useUrlModal(name, opts = {}) {
  // Normalize idKey shorthand into paramKeys
  let { paramKeys = null, idKey = null } = opts
  if (!paramKeys) {
    paramKeys = idKey ? [idKey] : []
  }
  const [sp, setSp] = useSearchParams()
  const navigate = useNavigate()
  const isOpen = sp.get('modal') === name

  const params = useMemo(() => {
    const out = {}
    if (!isOpen) return out
    for (const k of paramKeys) out[k] = sp.get(k)
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, sp, paramKeys.join(',')])

  const open = useCallback((payload) => {
    setSp((prev) => {
      const out = new URLSearchParams(prev)
      out.set('modal', name)
      // Clear any stale param values owned by this modal
      for (const k of paramKeys) out.delete(k)
      if (payload != null) {
        if (typeof payload === 'object') {
          for (const [k, v] of Object.entries(payload)) {
            if (v != null && v !== '') out.set(k, String(v))
          }
        } else if (paramKeys[0]) {
          out.set(paramKeys[0], String(payload))
        }
      }
      return out
    }, { replace: false })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setSp, name, paramKeys.join(',')])

  const close = useCallback(() => {
    const idx = window.history.state?.idx
    if (typeof idx === 'number' && idx > 0) {
      navigate(-1)
    } else {
      setSp((prev) => {
        const out = new URLSearchParams(prev)
        out.delete('modal')
        for (const k of paramKeys) out.delete(k)
        return out
      }, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setSp, navigate, paramKeys.join(',')])

  // Convenience: expose id for single-key modals
  const id = paramKeys.length > 0 ? params[paramKeys[0]] : null

  return { isOpen, id, params, open, close }
}
