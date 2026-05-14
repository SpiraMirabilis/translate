import { Link } from 'react-router-dom'

// Mirror of web/frontend/public/404.html (Apache ErrorDocument 404).
// Keep in sync if the design changes.
export default function NotFound({ publicLibrary = false }) {
  const homeTo = publicLibrary ? '/library' : '/'
  const homeLabel = publicLibrary ? 'Library' : 'Dashboard'

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-2xl bg-slate-800 border border-slate-700 rounded-xl overflow-hidden shadow-xl">
        <img
          src="/404.png"
          alt="A cultivator searches an old library for a missing scroll."
          className="w-full h-auto block"
        />
        <div className="p-6 sm:p-8 text-center">
          <p className="text-slate-300 mb-6">
            This scroll seems to have gone missing from the archives.
          </p>
          <Link
            to={homeTo}
            className="inline-block px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition-colors"
          >
            Return to the {homeLabel}
          </Link>
        </div>
      </div>
    </div>
  )
}
