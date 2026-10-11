import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, BookOpen, Github, Mail, Sun, Moon, Sunset } from 'lucide-react'
import { useReaderPrefs } from '../hooks/useReaderPrefs'
import { useSite } from '../App'
import { LIBRARY_THEME } from '../components/library/theme'

const REPO_URL = 'https://github.com/SpiraMirabilis/translate'

const THEME_TOGGLE = [
  { id: 'light', icon: Sun,    label: 'Light' },
  { id: 'sepia', icon: Sunset, label: 'Sepia' },
  { id: 'dark',  icon: Moon,   label: 'Dark'  },
]

export default function About() {
  const { prefs, setPrefs, theme } = useReaderPrefs()
  const { site_name, public_site_name } = useSite()
  const t = LIBRARY_THEME[prefs.theme] || LIBRARY_THEME.light

  useEffect(() => {
    document.title = `About Us — ${public_site_name}`
    return () => { document.title = site_name }
  }, [public_site_name, site_name])

  const h2 = `text-lg font-semibold ${t.title} mt-8 mb-2`
  const p = `${t.subtitle} leading-relaxed`

  return (
    <div className={`min-h-screen ${theme.bg} ${theme.text} transition-colors duration-300`}>
      <header className={`${t.headerBg} border-b ${t.headerBorder} shadow-sm transition-colors duration-300`}>
        <div className="max-w-3xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between gap-4">
          <Link to="/library" className={`inline-flex items-center gap-2 text-sm ${t.subtitle} hover:text-indigo-500 transition-colors`}>
            <ArrowLeft size={16} /> Library
          </Link>
          <div className={`flex items-center gap-0.5 ${t.toggleBg} rounded-lg p-1 transition-colors duration-300`}>
            {THEME_TOGGLE.map(({ id, icon: Icon, label }) => (
              <button key={id} onClick={() => setPrefs(prev => ({ ...prev, theme: id }))} title={label}
                className={`p-2 rounded-md transition-all duration-200 ${prefs.theme === id ? t.toggleActive : t.toggleInactive}`}>
                <Icon size={16} />
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 sm:px-6 py-10">
        <div className="flex items-center gap-3">
          <BookOpen size={28} className="text-indigo-500 shrink-0" />
          <h1 className={`text-2xl sm:text-3xl font-bold ${t.title} tracking-tight`}>About Us</h1>
        </div>

        <p className={`${p} mt-6`}>
          {public_site_name} is run by a small team of readers. We translate web novels,
          mostly Chinese, that we find interesting and want to read in English ourselves.
          The catalog follows our tastes rather than any schedule, so if you like what you
          find here, there is a good chance we like the same things.
        </p>

        <h2 className={h2}>How we translate</h2>
        <p className={p}>
          Every translation is produced with a combination of large language models, a
          custom pipeline we built for this work, and human editing:
        </p>
        <ul className={`${p} mt-3 space-y-2 list-disc pl-5`}>
          <li>
            <span className={`font-medium ${t.title}`}>Language models</span> produce the
            first draft of each chapter.
          </li>
          <li>
            <span className={`font-medium ${t.title}`}>The pipeline</span> keeps each book
            consistent: it maintains a glossary of names, places, techniques and titles
            for every novel and feeds it back into each chapter, so a character is called
            the same thing in chapter 900 as in chapter 9.
          </li>
          <li>
            <span className={`font-medium ${t.title}`}>Human editors</span> review the
            output, correct terminology across whole books, and add footnotes where a
            reference would otherwise be lost in translation.
          </li>
        </ul>
        <p className={`${p} mt-3`}>
          No process is perfect. If you spot a wrong name, a mistranslation or a typo,
          please use the Report an Error button in the reader or on the Library page;
          quoting the exact wording helps us find it quickly.
        </p>

        <h2 className={h2}>Open source</h2>
        <p className={p}>
          The translation pipeline and this website are open source.
        </p>
        <a href={REPO_URL} target="_blank" rel="noopener noreferrer"
          className="mt-4 inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition-colors">
          <Github size={16} /> SpiraMirabilis/translate on GitHub
        </a>

        <h2 className={h2}>Suggest a novel</h2>
        <p className={p}>
          Know a novel we should pick up? Use{' '}
          <Link to="/library?modal=recommend" className="text-indigo-500 hover:underline">Recommend a Novel</Link>.
          We read every suggestion, though we can only take on a few.
        </p>

        <h2 className={h2}>Contact</h2>
        <dl className={`${p} space-y-2`}>
          <div className="flex flex-wrap items-baseline gap-x-2">
            <dt>General questions and feedback:</dt>
            <dd>
              <a href="mailto:editor@boondollars.com" className="inline-flex items-center gap-1.5 text-indigo-500 hover:underline">
                <Mail size={14} /> editor@boondollars.com
              </a>
            </dd>
          </div>
          <div className="flex flex-wrap items-baseline gap-x-2">
            <dt>Copyright (DMCA) notices and abuse reports:</dt>
            <dd>
              <a href="mailto:abuse@boondollars.com" className="inline-flex items-center gap-1.5 text-indigo-500 hover:underline">
                <Mail size={14} /> abuse@boondollars.com
              </a>
            </dd>
          </div>
        </dl>
      </main>
    </div>
  )
}
