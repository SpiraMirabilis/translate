import { useState, useEffect, lazy, Suspense } from 'react'
import { api } from '../services/api'
import { Check, Eye, EyeOff, Loader2, RefreshCw, Download, X, FileJson } from 'lucide-react'
const CodeEditor = lazy(() => import('@uiw/react-textarea-code-editor'))

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

      {/* Unit Conversions */}
      <UnitsSection />

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
                    <CodeEditor
                      value={content}
                      language="json"
                      onChange={(e) => setContent(e.target.value)}
                      padding={16}
                      style={{
                        fontSize: 13,
                        fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
                        backgroundColor: '#0f172a',
                        minHeight: 200,
                        maxHeight: 450,
                        overflow: 'auto',
                      }}
                      data-color-mode="dark"
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

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-200 capitalize">{provider.name}</span>
          <span className={provider.has_key ? 'badge-emerald' : 'badge-slate'}>
            {provider.has_key ? 'Key set' : 'No key'}
          </span>
        </div>
        <button
          className="btn-secondary text-xs flex items-center gap-1"
          onClick={handleTest}
          disabled={testing || !provider.has_key}
        >
          {testing ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          Test
        </button>
      </div>

      <div className="text-xs text-slate-500">
        Default: <span className="font-mono text-slate-400">{provider.default_model}</span>
        {' · '}{provider.api_key_env}
      </div>

      {testResult && (
        <div className={`text-xs rounded px-2 py-1 ${testResult.ok ? 'bg-emerald-950 text-emerald-300' : 'bg-rose-950 text-rose-300'}`}>
          {testResult.ok ? `OK: ${testResult.msg}` : `Failed: ${testResult.msg}`}
        </div>
      )}

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
