import { Link } from 'react-router-dom'

export default function NotFound({ publicLibrary = false }) {
  const homeTo = publicLibrary ? '/library' : '/'
  const homeLabel = publicLibrary ? 'Library' : 'Dashboard'

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-slate-800 border border-slate-700 rounded-xl p-8 shadow-xl text-center">
        <div className="text-5xl font-bold text-slate-100 mb-2">404</div>
        <p className="text-slate-400 mb-6">
          The page you're looking for doesn't exist.
        </p>
        <Link
          to={homeTo}
          className="inline-block px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Go to {homeLabel}
        </Link>
      </div>
    </div>
  )
}
