import { useState, useEffect, lazy, Suspense } from 'react'
import { api } from '../services/api'
import { isNoCache, setNoCache } from '../services/cacheBust'
import { Check, Eye, EyeOff, Loader2, RefreshCw, Download, X, FileJson } from 'lucide-react'
const JsonCodeMirror = lazy(() => import('../components/JsonCodeMirror'))

export default function Settings() {
  const [providers, setProviders] = useState([])
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([api.listProviders(), api.getSettings()])
      .then(([pd, sd]) => {
        setProviders(pd.providers || [])
        setSettings(sd)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const handleSaveSettings = async () => {
    try {
      await api.updateSettings(settings)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e.message)
    }
  }

  const handleExportDb = async () => {
    try {
      const blob = await api.exportDb()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = 'entities.json'; a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message)
    }
  }

  if (loading) return (
    <div className="p-6 flex items-center gap-2 text-slate-400">
      <Loader2 size={14} className="animate-spin" /> Loading…
    </div>
  )

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-8">
      <h1 className="text-lg font-semibold text-slate-200">Settings</h1>

      {error && (
        <div className="card p-3 border-rose-800 bg-rose-950/40 text-rose-400 text-sm flex items-center justify-between">
          {error}
          <button onClick={() => setError(null)}><X size={14} /></button>
        </div>
      )}

      {/* API Providers */}
      <section>
        <h2 className="text-sm font-semibold text-slate-300 mb-3">API Providers</h2>
        <div className="space-y-3">
          {providers.map(p => (
            <ProviderCard key={p.name} provider={p} />
          ))}
        </div>
      </section>

      {/* Site Branding */}
      {settings && (
        <section>
          <h2 className="text-sm font-semibold text-slate-300 mb-3">Site Branding</h2>
          <div className="card p-4 space-y-4">
            <div>
              <label className="label">Site name</label>
              <input
                className="input text-sm"
                value={settings.site_name || ''}
                onChange={e => setSettings(s => ({ ...s, site_name: e.target.value }))}
                placeholder="T9"
              />
              <p className="text-xs text-slate-500 mt-1">Shown in the admin sidebar, login page, and EPUB intro. FastAPI title updates after a service restart.</p>
            </div>
            <div>
              <label className="label">Public site name</label>
              <input
                className="input text-sm"
                value={settings.public_site_name || ''}
                onChange={e => setSettings(s => ({ ...s, public_site_name: e.target.value }))}
                placeholder="Boonnovels"
              />
              <p className="text-xs text-slate-500 mt-1">Shown on the public reader (Library, book pages, RSS feed).</p>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn-primary flex items-center gap-1.5" onClick={handleSaveSettings}>
                {saved ? <Check size={13} /> : <Check size={13} />}
                {saved ? 'Saved!' : 'Save Settings'}
              </button>
            </div>
          </div>
        </section>
      )}

      {/* Model settings */}
      {settings && (
        <section>
          <h2 className="text-sm font-semibold text-slate-300 mb-3">Default Models</h2>
          <div className="card p-4 space-y-4">
            <div>
              <label className="label">Translation model</label>
              <input
                className="input font-mono text-sm"
                value={settings.translation_model || ''}
                onChange={e => setSettings(s => ({ ...s, translation_model: e.target.value }))}
                placeholder="e.g. claude:claude-sonnet-4-6"
              />
              <p className="text-xs text-slate-500 mt-1">Format: provider:model-name</p>
            </div>
            <div>
              <label className="label">Advice model</label>
              <input
                className="input font-mono text-sm"
                value={settings.advice_model || ''}
                onChange={e => setSettings(s => ({ ...s, advice_model: e.target.value }))}
                placeholder="e.g. oai:o3-mini"
              />
            </div>
            <div>
              <label className="label">Pronoun repair model</label>
              <input
                className="input font-mono text-sm"
                value={settings.pronoun_repair_model || ''}
                onChange={e => setSettings(s => ({ ...s, pronoun_repair_model: e.target.value }))}
                placeholder="e.g. claude:claude-haiku-4-5"
              />
              <p className="text-xs text-slate-500 mt-1">Small classifier used to fix wrong-gender pronouns after entity gender changes. A fast/cheap model is recommended (default: claude:claude-haiku-4-5).</p>
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.debug_mode || false}
                onChange={e => setSettings(s => ({ ...s, debug_mode: e.target.checked }))}
              />
              Debug mode
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.public_library !== false}
                onChange={e => setSettings(s => ({ ...s, public_library: e.target.checked }))}
              />
              Public library
              <span className="text-xs text-slate-500 font-normal">— allow unauthenticated access to the reader and library pages</span>
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.disable_content_cache || false}
                onChange={e => setSettings(s => ({ ...s, disable_content_cache: e.target.checked }))}
              />
              Disable server-side caching of chapter/text content
              <span className="text-xs text-slate-500 font-normal">— readers see chapter corrections immediately; turn off again to restore caching</span>
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.disable_media_cache || false}
                onChange={e => setSettings(s => ({ ...s, disable_media_cache: e.target.checked }))}
              />
              Disable server-side caching of media (covers, illustrations, EPUBs)
              <span className="text-xs text-slate-500 font-normal">— rarely needed; high-byte items that seldom change</span>
            </label>
            <div>
              <label className="label">JSON fix timeout (seconds)</label>
              <input
                className="input text-sm"
                type="number"
                min="0"
                value={settings.json_fix_timeout_seconds ?? 300}
                onChange={e => setSettings(s => ({ ...s, json_fix_timeout_seconds: parseInt(e.target.value || '0', 10) }))}
                placeholder="300"
              />
              <p className="text-xs text-slate-500 mt-1">How long the JSON Fix modal waits for manual input before defaulting to "Retry Chunk" so unattended jobs don't hang. 0 = wait forever.</p>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn-primary flex items-center gap-1.5" onClick={handleSaveSettings}>
                {saved ? <Check size={13} /> : <Check size={13} />}
                {saved ? 'Saved!' : 'Save Settings'}
              </button>
            </div>
          </div>
        </section>
      )}

      {/* WordPress */}
      <WordPressSection />

      {/* Email Notifications */}
      {settings && (
        <section>
          <h2 className="text-sm font-semibold text-slate-300 mb-3">Email Notifications</h2>
          <div className="card p-4 space-y-4">
            <p className="text-xs text-slate-500">
              Used for reply-notification emails (sent via local Postfix). Leave blank to disable outgoing email.
            </p>
            <div>
              <label className="label">Sender address (EMAIL_FROM)</label>
              <input
                className="input text-sm"
                value={settings.email_from || ''}
                onChange={e => setSettings(s => ({ ...s, email_from: e.target.value }))}
                placeholder="noreply@yourdomain.com"
              />
              <p className="text-xs text-slate-500 mt-1">Must be a domain Postfix is authorized to send from. Notifications won't deliver if this is unset.</p>
            </div>
            <div>
              <label className="label">Site base URL (SITE_BASE_URL)</label>
              <input
                className="input text-sm"
                value={settings.site_base_url || ''}
                onChange={e => setSettings(s => ({ ...s, site_base_url: e.target.value }))}
                placeholder="https://reader.yourdomain.com"
              />
              <p className="text-xs text-slate-500 mt-1">Public base URL of the reader site. Used to build absolute links to chapters and unsubscribe endpoints in outgoing emails.</p>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn-primary flex items-center gap-1.5" onClick={handleSaveSettings}>
                <Check size={13} />
                {saved ? 'Saved!' : 'Save Settings'}
              </button>
            </div>
          </div>
        </section>
      )}

      {/* Comment Moderation */}
      {settings && (
        <section>
          <h2 className="text-sm font-semibold text-slate-300 mb-3">Comment Moderation</h2>
          <div className="card p-4 space-y-4">
            <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
              <input
                type="checkbox"
                checked={settings.comment_automod_enabled || false}
                onChange={e => setSettings(s => ({ ...s, comment_automod_enabled: e.target.checked }))}
              />
              Enable AI auto-moderation of new comments
              <span className="text-xs text-slate-500 font-normal">— flags spam / abuse asynchronously after submission</span>
            </label>
            <div>
              <label className="label">Auto-moderation model</label>
              <input
                className="input font-mono text-sm"
                value={settings.comment_automod_model || ''}
                onChange={e => setSettings(s => ({ ...s, comment_automod_model: e.target.value }))}
                placeholder="e.g. claude:claude-haiku-4-5"
              />
              <p className="text-xs text-slate-500 mt-1">A fast/cheap model is recommended since every new comment is scanned.</p>
            </div>
            <div className="flex items-center gap-2">
              <button className="btn-primary flex items-center gap-1.5" onClick={handleSaveSettings}>
                <Check size={13} />
                {saved ? 'Saved!' : 'Save Settings'}
              </button>
            </div>
          </div>
        </section>
      )}

      {/* Unit Conversions */}
      <UnitsSection />

      {/* Developer */}
      <DeveloperSection />

      {/* Database */}
      <section>
        <h2 className="text-sm font-semibold text-slate-300 mb-3">Database</h2>
        <div className="card p-4">
          <p className="text-sm text-slate-400 mb-3">Export all entities as JSON for backup or migration.</p>
          <button className="btn-secondary flex items-center gap-1.5" onClick={handleExportDb}>
            <Download size={13} /> Export entities.json
          </button>
        </div>
      </section>
    </div>
  )
}

function DeveloperSection() {
  // Frontend-only, localStorage-backed toggle — applies instantly (no service
  // restart) and only to this browser. See services/cacheBust.js.
  const [noCache, setNoCacheState] = useState(isNoCache())

  const toggle = (on) => {
    setNoCache(on)
    setNoCacheState(on)
  }

  return (
    <section>
      <h2 className="text-sm font-semibold text-slate-300 mb-3">Developer</h2>
      <div className="card p-4 space-y-4">
        <label className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
          <input
            type="checkbox"
            checked={noCache}
            onChange={e => toggle(e.target.checked)}
          />
          Disable caching of API calls &amp; assets
          <span className="text-xs text-slate-500 font-normal">— see book/chapter/cover edits immediately</span>
        </label>
        <p className="text-xs text-slate-500">
          Sends every API request with <code className="text-slate-400">cache: no-store</code> and busts cover /
          illustration image URLs so the browser refetches them on reload. This setting lives in your browser only
          (not the server) and takes effect immediately — leave it off in normal use to keep things snappy.
        </p>
      </div>
    </section>
  )
}

function UnitsSection() {
  const [open, setOpen] = useState(false)
  const [content, setContent] = useState('')
  const [original, setOriginal] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [validationError, setValidationError] = useState('')

  const load = async () => {
    setLoading(true); setError(null)
    try {
      const res = await api.getUnits()
      // Pretty-print for editing
      const pretty = JSON.stringify(JSON.parse(res.content), null, 2)
      setContent(pretty)
      setOriginal(pretty)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleOpen = () => {
    setOpen(true)
    load()
  }

  // Validate on every edit
  useEffect(() => {
    if (!open) return
    try {
      JSON.parse(content)
      setValidationError('')
    } catch (e) {
      setValidationError(e.message)
    }
  }, [content, open])

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      await api.updateUnits({ content })
      setOriginal(content)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const dirty = content !== original

  return (
    <section>
      <h2 className="text-sm font-semibold text-slate-300 mb-3">Unit Conversions</h2>
      <div className="card p-4">
        {!open ? (
          <>
            <p className="text-sm text-slate-400 mb-3">
              Configure how Chinese measurement units are converted in translated text.
            </p>
            <button className="btn-secondary flex items-center gap-1.5" onClick={handleOpen}>
              <FileJson size={13} /> Edit units.json
            </button>
          </>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              Each entry maps a unit name to its metric value, unit type, and action (<code className="text-slate-400">annotate</code> adds a parenthetical, <code className="text-slate-400">replace</code> substitutes it). Optional <code className="text-slate-400">numeral</code>: <code className="text-slate-400">arabic</code> (default) or <code className="text-slate-400">english</code> for word numerals.
            </p>
            {loading ? (
              <div className="flex items-center gap-2 text-slate-400 text-sm py-4">
                <Loader2 size={14} className="animate-spin" /> Loading...
              </div>
            ) : (
              <>
                <div className="rounded-lg overflow-hidden border border-slate-700">
                  <Suspense fallback={<div className="p-4 text-slate-400 text-sm">Loading editor…</div>}>
                    <JsonCodeMirror
                      value={content}
                      onChange={(val) => setContent(val)}
                      minHeight="200px"
                      maxHeight="450px"
                    />
                  </Suspense>
                </div>
                {validationError && (
                  <div className="flex items-center gap-2 text-xs">
                    <X size={14} className="text-rose-400" />
                    <span className="text-rose-400">{validationError}</span>
                  </div>
                )}
                {error && <p className="text-rose-400 text-xs">{error}</p>}
                <div className="flex items-center gap-2">
                  <button
                    className="btn-primary flex items-center gap-1.5"
                    onClick={handleSave}
                    disabled={saving || !dirty || !!validationError}
                  >
                    {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                    {saved ? 'Saved!' : 'Save'}
                  </button>
                  <button className="btn-secondary" onClick={() => setOpen(false)}>Close</button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

function ProviderCard({ provider }) {
  const [key, setKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [error, setError] = useState(null)

  const handleSaveKey = async () => {
    if (!key.trim()) return
    setSaving(true); setError(null)
    try {
      await api.setApiKey(provider.name, { api_key: key })
      setKey('')
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true); setTestResult(null); setError(null)
    try {
      const r = await api.testProvider(provider.name)
      setTestResult({ ok: true, msg: r.response })
    } catch (e) {
      setTestResult({ ok: false, msg: e.message })
    } finally {
      setTesting(false)
    }
  }

  const cliAuth = !provider.api_key_env

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-200 capitalize">{provider.name}</span>
          <span className={cliAuth || provider.has_key ? 'badge-emerald' : 'badge-slate'}>
            {cliAuth ? 'CLI auth' : provider.has_key ? 'Key set' : 'No key'}
          </span>
        </div>
        <button
          className="btn-secondary text-xs flex items-center gap-1"
          onClick={handleTest}
          disabled={testing || (!cliAuth && !provider.has_key)}
        >
          {testing ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          Test
        </button>
      </div>

      <div className="text-xs text-slate-500">
        Default: <span className="font-mono text-slate-400">{provider.default_model}</span>
        {provider.api_key_env && <>{' · '}{provider.api_key_env}</>}
      </div>

      {testResult && (
        <div className={`text-xs rounded px-2 py-1 ${testResult.ok ? 'bg-emerald-950 text-emerald-300' : 'bg-rose-950 text-rose-300'}`}>
          {testResult.ok ? `OK: ${testResult.msg}` : `Failed: ${testResult.msg}`}
        </div>
      )}

      {!cliAuth && (
        <div className="flex gap-2">
          <div className="relative flex-1">
            <input
              type={showKey ? 'text' : 'password'}
              className="input pr-8 text-sm font-mono"
              placeholder={`Enter ${provider.api_key_env}…`}
              value={key}
              onChange={e => setKey(e.target.value)}
            />
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              onClick={() => setShowKey(v => !v)}
            >
              {showKey ? <EyeOff size={13} /> : <Eye size={13} />}
            </button>
          </div>
          <button
            className="btn-secondary flex items-center gap-1 shrink-0"
            onClick={handleSaveKey}
            disabled={saving || !key.trim()}
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
            Set
          </button>
        </div>
      )}

      {error && <p className="text-rose-400 text-xs">{error}</p>}
    </div>
  )
}

function WordPressSection() {
  const [wp, setWp] = useState({ wp_url: '', wp_username: '', wp_app_password: '' })
  const [showPw, setShowPw] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api.wpGetSettings()
      .then(d => {
        setWp({ wp_url: d.wp_url || '', wp_username: d.wp_username || '', wp_app_password: '' })
        setLoaded(true)
      })
      .catch(e => setError(e.message))
  }, [])

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      const body = { wp_url: wp.wp_url, wp_username: wp.wp_username }
      if (wp.wp_app_password) body.wp_app_password = wp.wp_app_password
      await api.wpUpdateSettings(body)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true); setTestResult(null); setError(null)
    try {
      const r = await api.wpTestConnection()
      setTestResult({ ok: true, msg: `Connected to "${r.site_name}"` })
    } catch (e) {
      setTestResult({ ok: false, msg: e.message })
    } finally {
      setTesting(false)
    }
  }

  if (!loaded) return null

  return (
    <section>
      <h2 className="text-sm font-semibold text-slate-300 mb-3">WordPress / Fictioneer</h2>
      <div className="card p-4 space-y-3">
        <p className="text-xs text-slate-500">
          Connect to a WordPress site with the Fictioneer theme to publish books and chapters.
          Use an <a href="https://make.wordpress.org/core/2020/11/05/application-passwords-integration-guide/" target="_blank" rel="noopener" className="text-blue-400 hover:underline">Application Password</a> for authentication.
        </p>
        <div>
          <label className="label">WordPress Site URL</label>
          <input
            className="input text-sm"
            value={wp.wp_url}
            onChange={e => setWp(s => ({ ...s, wp_url: e.target.value }))}
            placeholder="https://your-site.com"
          />
        </div>
        <div>
          <label className="label">Username</label>
          <input
            className="input text-sm"
            value={wp.wp_username}
            onChange={e => setWp(s => ({ ...s, wp_username: e.target.value }))}
            placeholder="admin"
          />
        </div>
        <div>
          <label className="label">Application Password</label>
          <div className="relative">
            <input
              type={showPw ? 'text' : 'password'}
              className="input pr-8 text-sm font-mono"
              value={wp.wp_app_password}
              onChange={e => setWp(s => ({ ...s, wp_app_password: e.target.value }))}
              placeholder="xxxx xxxx xxxx xxxx xxxx xxxx"
            />
            <button
              className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              onClick={() => setShowPw(v => !v)}
            >
              {showPw ? <EyeOff size={13} /> : <Eye size={13} />}
            </button>
          </div>
        </div>

        {testResult && (
          <div className={`text-xs rounded px-2 py-1 ${testResult.ok ? 'bg-emerald-950 text-emerald-300' : 'bg-rose-950 text-rose-300'}`}>
            {testResult.ok ? testResult.msg : `Failed: ${testResult.msg}`}
          </div>
        )}

        {error && <p className="text-rose-400 text-xs">{error}</p>}

        <div className="flex items-center gap-2">
          <button className="btn-primary flex items-center gap-1.5" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            {saved ? 'Saved!' : 'Save'}
          </button>
          <button
            className="btn-secondary flex items-center gap-1"
            onClick={handleTest}
            disabled={testing}
          >
            {testing ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
            Test Connection
          </button>
        </div>
      </div>
    </section>
  )
}
