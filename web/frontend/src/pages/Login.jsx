import { useState } from 'react'
import { api } from '../services/api'

export default function Login({ onSuccess }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await api.login({ password })
      onSuccess()
    } catch (err) {
      setError(err.message || 'Wrong password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-sm bg-slate-800 border border-slate-700 rounded-xl p-8 shadow-xl">
        <h1 className="text-2xl font-bold text-slate-100 text-center mb-2">T9 Translation</h1>
        <p className="text-slate-400 text-center text-sm mb-6">Enter your password to continue</p>

        <form onSubmit={handleSubmit}>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            autoFocus
            className="w-full px-4 py-2.5 bg-slate-900 border border-slate-600 rounded-lg
                       text-slate-200 placeholder-slate-500
                       focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500
                       mb-4"
          />

          {error && (
            <p className="text-red-400 text-sm mb-4">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !password}
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600
                       disabled:cursor-not-allowed text-white font-medium rounded-lg
                       transition-colors"
          >
            {loading ? 'Logging in...' : 'Log in'}
          </button>
        </form>
      </div>
    </div>
  )
}
